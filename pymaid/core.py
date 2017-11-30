#    Copyright (C) 2017 Philipp Schlegel

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.

#    You should have received a copy of the GNU General Public License
#    along

""" This module contains neuron and neuronlist classes returned and accepted
by many functions within pymaid. CatmaidNeuron and CatmaidNeuronList objects
also provided quick access to many other PyMaid functions.

Examples
--------
>>> # Get a bunch of neurons from CATMAID server as CatmaidNeuronList
>>> nl = pymaid.get_neuron( 'annotation:uPN right' ) 
>>> # CatmaidNeuronLists work in, many ways, like pandas DataFrames
>>> nl.head()
                            neuron_name skeleton_id  n_nodes  n_connectors  \
0              PN glomerulus VA6 017 DB          16    12721          1878   
1          PN glomerulus VL2a 22000 JMR       21999    10740          1687   
2            PN glomerulus VC1 22133 BH       22132     8446          1664   
3  PN putative glomerulus VC3m 22278 AA       22277     6228           674   
4          PN glomerulus DL2v 22423 JMR       22422     4610           384   

   n_branch_nodes  n_end_nodes  open_ends  cable_length review_status  soma  
0             773          822        280   2863.743284            NA  True  
1             505          537        194   2412.045343            NA  True  
2             508          548        110   1977.235899            NA  True  
3             232          243        100   1221.985849            NA  True  
4             191          206         93   1172.948499            NA  True  
>>> # Plot neurons 
>>> nl.plot3d()
>>> # Neurons in a list can be accessed by index, ...
>>> nl[0]
>>> # ... by skeleton ID, ...
>>> nl.skid[16]
>>> # ... or by attributes
>>> nl[ nl.cable_length > 2000 ]
>>> # Each neuron has a bunch of useful attributes
>>> print( nl[0].skeleton_id, nl[0].soma, nl[0].n_open_ends )
>>> # Attributes can also be accessed for the entire neuronslist
>>> nl.skeleton_id

:class:`~pymaid.CatmaidNeuron` and :class:`~pymaid.CatmaidNeuronList`
also allow quick access to other PyMaid functions:

>>> # This ...
>>> pymaid.reroot_neuron( nl[0], nl[0].soma, inplace=True )
>>> # ... is essentially equivalent to this
>>> nl[0].reroot( nl[0].soma )
>>> # Similarly, CatmaidNeurons do on-demand data fetching for you:
>>> # So instead of this ...
>>> an = pymaid.get_annotations( nl[0] )
>>> # ..., you can do just this:
>>> an = nl[0].annotations

"""

import datetime
import logging
import pandas as pd
import numpy as np
import math
import datetime
import random
import json
import os
from tqdm import tqdm
from copy import copy, deepcopy
import csv
import sys
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, as_completed
import scipy

from pymaid import igraph_catmaid, morpho, pymaid, plotting

__all__ = ['CatmaidNeuron','CatmaidNeuronList','Dotprops','Volume']

# Set up logging
module_logger = logging.getLogger(__name__)
module_logger.setLevel(logging.INFO)
if len( module_logger.handlers ) == 0:
    # Generate stream handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    # Create formatter and add it to the handlers
    formatter = logging.Formatter(
                '%(levelname)-5s : %(message)s (%(name)s)')
    sh.setFormatter(formatter)
    module_logger.addHandler(sh)


class CatmaidNeuron:
    """ Catmaid neuron object holding neuron data (nodes, connectors, name, 
    etc) and providing quick access to various PyMaid functions.

    Notes
    -----
    CatmaidNeuron can be minimally constructed from just a skeleton ID
    and a CatmaidInstance. Other parameters (nodes, connectors, neuron name, 
    annotations, etc.) will then be retrieved from the server 'on-demand'.

    The easiest way to construct a CatmaidNeuron is by using
    :func:`~pymaid.get_neuron`. 

    Manually, a complete CatmaidNeuron can be constructed from a pandas 
    DataFrame (df) containing: df.nodes, df.connectors, df.skeleton_id, 
    df.neuron_name, df.tags

    Using a CatmaidNeuron to initialise a CatmaidNeuron will automatically
    make a copy.

    Parameters
    ----------
    x                   {skeletonID, CatmaidNeuron}
                        Data to construct neuron from.                        
    remote_instance :   CatmaidInstance, optional
                        Storing this makes it more convenient to retrieve e.g. 
                        neuron annotations, review status, etc.
    meta_data :         dict, optional
                        any additional data


    Attributes
    ----------
    skeleton_id :       str
                        This neurons skeleton ID
    neuron_name :       str
                        This neurons name
    nodes :             ``pandas.DataFrame``
                        Contains complete treenode table
    connectors :        ``pandas.DataFrame``
                        Contains complete connector table
    presynapses :       ``pandas.DataFrame``
                        All presynaptic connectors
    postsynapses :      ``pandas.DataFrame``
                        All postsynaptic connectors
    gap_junctions :     ``pandas.DataFrame``
                        All gap junction connectors
    date_retrieved :    ``datetime`` object
                        Timestamp of data retrieval
    tags :              dict
                        Treenode tags
    annotations :       list
                        This neuron's annotations
    igraph :            ``iGraph`` object
                        iGraph representation of this neuron
    dps :               ``pandas.DataFrame``
                        Dotproduct representation of this neuron.
    review_status :     int
                        This neuron's review status
    n_connectors :      int
                        Total number of synapses
    n_presynapses :     int
                        Total number of presynaptic sites
    n_postsynapses :    int
                        Total number of presynaptic sites
    n_branch_nodes :    int
                        Number of branch nodes
    n_end_nodes :       int
                        Number of end nodes
    n_open_ends :       int
                        Number of open end nodes. Leaf nodes that are not 
                        tagged with either: 'ends', 'not a branch', 
                        'uncertain end', 'soma' or 'uncertain continuation'
    cable_length :      float
                        Cable length in micrometers [um]
    segments :          list of treenode IDs
                        Linear segments of the neuron
    soma :              treenode_id of soma
                        Returns None if no soma or 'NA' if data not available
    root :              treenode_id of root
    color :             tuple
                        Color of neuron. Used for e.g. export to json.

    Examples
    --------
    >>> import pymaid    
    >>> # Initialize a new neuron
    >>> n = pymaid.CatmaidNeuron( 123456 ) 
    >>> # Initialize Catmaid connections
    >>> rm = pymaid.CatmaidInstance(server_url, http_user, http_pw, token) 
    >>> # Add CatmaidInstance to the neuron for convenience    
    >>> n.remote_instance = rm 
    >>> # Retrieve node data from server on-demand
    >>> n.nodes 
    ... CatmaidNeuron - INFO - Retrieving skeleton data...
    ...    treenode_id  parent_id  creator_id  x  y  z radius confidence
    ... 0  ...
    ...
    >>> # Initialize with skeleton data
    >>> n = pymaid.get_neuron( 123456, remote_instance = rm )
    >>> # Get annotations from server
    >>> n.annotations
    ... [ 'annotation1', 'annotation2' ]
    >>> # Force update of annotations
    >>> n.get_annotations()

    """

    def __init__(self, x, remote_instance=None, meta_data=None):
        if isinstance(x, pd.DataFrame) or isinstance(x, CatmaidNeuronList):
            if x.shape[0] == 1:
                x = x.ix[0]
            else:
                raise Exception(
                    'Unable to construct CatmaidNeuron from data containing multiple neurons. Try CatmaidNeuronList instead.')

        if not isinstance(x, str) and not isinstance(x, int) and not isinstance(x, pd.Series) and not isinstance(x, CatmaidNeuron):
            raise TypeError(
                'Unable to construct CatmaidNeuron from data type %s' % str(type(x)))

        if remote_instance is None:
            if 'remote_instance' in sys.modules:
                remote_instance = sys.modules['remote_instance']
            elif 'remote_instance' in globals():
                remote_instance = globals()['remote_instance']

        # These will be overriden if x is a CatmaidNeuron
        self._remote_instance = remote_instance
        self._meta_data = meta_data
        self.date_retrieved = datetime.datetime.now().isoformat()

        # Parameters for soma detection
        self.soma_detection_radius = 500
        # Soma tag - set to None if no tag needed
        self.soma_detection_tag = 'soma'

        #Default color is yellow
        self.color = (255, 255, 0)

        if not isinstance(x, (CatmaidNeuron, pd.Series, pd.DataFrame)):
            try:
                int(x)  # Check if this is a skeleton ID
                self.skeleton_id = str(x) # Make sure skid is a string
            except:
                raise Exception(
                    'Unable to construct CatmaidNeuron from data provided: %s' % str(type(x)))
        else:
            # If we are dealing with a DataFrame or a CatmaidNeuron get the essentials
            essential_attributes = [ 'skeleton_id', 'neuron_name', 'nodes', 'connectors', 'tags' ]

            for at in essential_attributes:
                setattr(self, at, getattr(x, at ) )

            if 'type' not in self.nodes:
                morpho.classify_nodes(self)

            # Now move additional attributes
            for at in x.__dict__:
                if at not in essential_attributes:
                    setattr(self, at, getattr(x, at ) )

            # If a CatmaidNeuron is used to initialize, we need to make this 
            # new object independent by copying important attributes
            if isinstance(x, CatmaidNeuron):
                for at in self.__dict__:
                    try:
                        # Simple attributes don't have a .copy()
                        setattr( self, at, getattr(self, at).copy() )
                    except:
                        pass


    def __getattr__(self, key):
        # This is to catch empty neurons (e.g. after pruning)
        if 'nodes' in self.__dict__  and self.nodes.empty and key in ['n_open_ends','n_branch_nodes','n_end_nodes','cable_length']:
            return 0

        if key == 'igraph':
            return self.get_igraph()
        elif key == 'dps':
            return self.get_dps()
        elif key == 'nodes_geodesic_distance_matrix':
            module_logger.info('Creating geodesic distance matrix for treenodes...')
            self.nodes_geodesic_distance_matrix = self.igraph.shortest_paths_dijkstra(mode='All', weights='weight')
            return self.nodes_geodesic_distance_matrix
        elif key == 'neuron_name':
            return self.get_name()
        elif key == 'annotations':
            return self.get_annotations()
        elif key == 'review_status':
            return self.get_review()
        elif key == 'nodes':
            self.get_skeleton()
            return self.nodes
        elif key == 'connectors':
            self.get_skeleton()
            return self.connectors
        elif key == 'presynapses':
            return self.connectors[ self.connectors.relation == 0 ].reset_index()
        elif key == 'postsynapses':
            return self.connectors[ self.connectors.relation == 1 ].reset_index()
        elif key == 'gap_junctions':
            return self.connectors[ self.connectors.relation == 2 ].reset_index()
        elif key == 'segments':
            self._get_segments()
            return self.segments
        elif key == 'soma':
            return self._get_soma()
        elif key == 'root':
            return self._get_root()
        elif key == 'tags':
            self.get_skeleton()
            return self.tags
        elif key == 'n_open_ends':
            if 'nodes' in self.__dict__:
                closed = self.tags.get('ends', []) + self.tags.get('uncertain end', []) + self.tags.get(
                    'uncertain continuation', []) + self.tags.get('not a branch', []) + self.tags.get('soma', [])
                return len([n for n in self.nodes[self.nodes.type == 'end'].treenode_id.tolist() if n not in closed])
            else:
                return 'NA'
        elif key == 'n_branch_nodes':
            if 'nodes' in self.__dict__:
                return self.nodes[self.nodes.type == 'branch'].shape[0]
            else:
                return 'NA'
        elif key == 'n_end_nodes':
            if 'nodes' in self.__dict__:
                return self.nodes[self.nodes.type == 'end'].shape[0]
            else:
                return 'NA'
        elif key == 'n_nodes':
            if 'nodes' in self.__dict__:
                return self.nodes.shape[0]
            else:
                return 'NA'
        elif key == 'n_connectors':
            if 'connectors' in self.__dict__:
                return self.connectors.shape[0]
            else:
                return 'NA'
        elif key == 'n_presynapses':
            if 'connectors' in self.__dict__:
                return self.connectors[ self.connectors.relation == 0 ].shape[0]
            else:
                return 'NA'
        elif key == 'n_postsynapses':
            if 'connectors' in self.__dict__:
                return self.connectors[ self.connectors.relation == 1 ].shape[0]
            else:
                return 'NA'        
        elif key == 'cable_length':
            if 'nodes' in self.__dict__:
                # Simply sum up edge weight of all igraph edges
                return sum( self.igraph.es['weight'] ) / 1000
            else:
                return 'NA'
        else:
            raise AttributeError('Attribute "%s" not found' % key)

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self):
        return self.copy()

    def copy(self):
        """Returns a copy of the neuron."""
        return CatmaidNeuron(self)

    def get_skeleton(self, remote_instance=None, **kwargs):
        """Get/Update skeleton data for neuron.

        Parameters
        ----------       
        **kwargs
                    Will be passed to pymaid.get_neuron()
                    e.g. to get the full treenode history use:
                    n.get_skeleton( with_history = True )
                    or to get abutting connectors:
                    n.get_skeleton( get_abutting = True )

        See Also
        --------
        :func:`~pymaid.get_neuron` 
                    Function called to get skeleton information
        """
        if not remote_instance and not self._remote_instance:
            raise Exception(
                'Get_skeleton - Unable to connect to server without remote_instance. See help(core.CatmaidNeuron) to learn how to assign.')
        elif not remote_instance:
            remote_instance = self._remote_instance
        module_logger.info('Retrieving skeleton data...')
        skeleton = pymaid.get_neuron(
            self.skeleton_id, remote_instance, return_df=True, kwargs=kwargs).ix[0]       

        self.nodes = skeleton.nodes
        self.connectors = skeleton.connectors
        self.tags = skeleton.tags
        self.neuron_name = skeleton.neuron_name
        self.date_retrieved = datetime.datetime.now().isoformat()

        # Delete outdated attributes
        self._clear_temp_attr()

        if 'type' not in self.nodes:
            morpho.classify_nodes(self)

        return

    def _clear_temp_attr(self):
        """Clear temporary attributes."""
        for a in ['igraph','segments','nodes_geodesic_distance_matrix','dps']:
            try:
                delattr(self, a)
            except:
                pass

        # Remove temporary node values
        for c in ['flow_centrality','type']:
            if c in self.nodes:
                self.nodes.drop( c, axis=1, inplace=True)

        #Reclassify nodes
        morpho.classify_nodes(self, inplace=True)

    def get_igraph(self):
        """Calculates iGraph representation of neuron. Once calculated stored
        as `.igraph`. Call function again to update iGraph. 


        See Also
        --------
        :func:`pymaid.neuron2graph`
        """
        level = igraph_catmaid.module_logger.level
        igraph_catmaid.module_logger.setLevel('WARNING')
        self.igraph = igraph_catmaid.neuron2graph(self)
        igraph_catmaid.module_logger.setLevel(level)
        return self.igraph

    def get_dps(self):
        """Calculates dotproduct representation of neuron. Once calculated stored
        as `.igraph`. Call function again to update iGraph.

        See Also
        --------
        :func:`pymaid.to_dotproduct`
        """

        self.dps = morpho.to_dotproduct( self )
        return self.dps

    def _get_segments(self):
        """Generate segments for neuron."""
        module_logger.debug('Generating segments for neuron %s' % str(self.skeleton_id))
        self.segments = morpho._generate_segments(self)
        return self.segments

    def _get_soma(self):
        """Search for soma and return treenode ID of soma.

        Notes
        -----
        Uses either a treenode tag or treenode radius or a combination of both
        to identify the soma. This is set in the class attributes 
        ``soma_detection_radius`` and ``soma_detection_tag``. The default
        values for these are::


                soma_detection_radius = 100 
                soma_detection_tag = 'soma'


        Returns
        -------
        treenode_id
            Returns treenode ID if soma was found, None if no soma.

        """
        tn = self.nodes[self.nodes.radius >
                        self.soma_detection_radius].treenode_id.tolist()

        if self.soma_detection_tag:
            if self.soma_detection_tag not in self.tags:
                return None
            else:
                tn = [n for n in tn if n in self.tags[self.soma_detection_tag]]

        if len(tn) == 1:
            return tn[0]
        elif len(tn) == 0:
            return None

        module_logger.warning('%s: Multiple possible somas found' % self.skeleton_id)
        return tn

    def _get_root(self):
        """Thin wrapper to get root node."""
        roots = self.nodes[self.nodes.parent_id.isnull()].treenode_id.tolist()[0] 
        if isinstance(roots, (list, np.ndarray)) and len(roots) == 1:
            return roots[0]
        else:
            return roots

    def get_review(self, remote_instance=None):
        """Get review status for neuron."""
        if not remote_instance and not self._remote_instance:
            module_logger.error(
                'Get_review: Unable to connect to server. Please provide CatmaidInstance as <remote_instance>.')
            return None
        elif not remote_instance:
            remote_instance = self._remote_instance
        self.review_status = pymaid.get_review(self.skeleton_id, remote_instance).ix[
            0].percent_reviewed
        return self.review_status

    def get_annotations(self, remote_instance=None):
        """Retrieve annotations for neuron."""
        if not remote_instance and not self._remote_instance:
            module_logger.error(
                'Get_annotations: Need CatmaidInstance to retrieve annotations. Use neuron.get_annotations( remote_instance = CatmaidInstance )')
            return None
        elif not remote_instance:
            remote_instance = self._remote_instance

        self.annotations = pymaid.get_annotations(
            self.skeleton_id, remote_instance)[str(self.skeleton_id)]
        return self.annotations

    def plot2d(self, **kwargs):
        """Plot neuron using pymaid.plot.plot2d().  

        Parameters
        ----------     
        **kwargs         
                Will be passed to plot2d() 
                See help(pymaid.plot3d) for a list of keywords  

        See Also
        --------
        :func:`pymaid.plot2d` 
                    Function called to generate 2d plot
        """
        if 'nodes' not in self.__dict__:
            self.get_skeleton()
        return plotting.plot2d(self, **kwargs)

    def plot3d(self, **kwargs):
        """Plot neuron using pymaid.plot.plot3d().  

        Parameters
        ----------      
        **kwargs
                Will be passed to plot3d() 
                See help(pymaid.plot3d) for a list of keywords      

        See Also
        --------
        :func:`pymaid.plot3d` 
                    Function called to generate 3d plot   

        Examples
        --------
        >>> nl = pymaid.get_neuron('annotation:uPN right')
        >>> #Plot with connectors
        >>> nl.plot3d( connectors=True )               
        """

        if 'remote_instance' not in kwargs:
            kwargs.update({'remote_instance': self._remote_instance})

        if 'nodes' not in self.__dict__:
            self.get_skeleton()
        return plotting.plot3d(CatmaidNeuronList(self, make_copy=False), **kwargs)

    def plot_dendrogram(self, linkage_kwargs={}, dend_kwargs={}):
        """ Plot neuron as dendrogram.

        Parameters
        ----------
        linkage_kwargs :    dict
                            Will be passed to scipy.hierarchy.linkage
        dend_kwargs :       dict
                            Will be passed to scipy.hierarchy.dendrogram

        Returns
        -------
        scipy.hierarchy.dendrogram
        """             

        # First get the all by all distances
        # need to change THIS to get_all_shortest_paths
        dist_mat = self.nodes_geodesic_distance_matrix

        # Remove non end nodes
        non_leaf_ix = [ v.index for v in self.igraph.vs if v['node_id'] not in self.nodes[ self.nodes.type == 'end' ].treenode_id.values ]        

        ends_mat = np.delete(dist_mat, non_leaf_ix, 0)
        ends_mat = np.delete(ends_mat, non_leaf_ix, 1)        

        #Cluster
        linkage = scipy.hierarchy.linkage( ends_mat, **linkage_kwargs )

        #Plot
        return scipy.hierarchy.dendrogram(linkage, **dend_kwargs)


    def get_name(self, remote_instance=None):
        """Retrieve/update name of neuron."""
        if not remote_instance and not self._remote_instance:
            module_logger.error(
                'Get_name: Need CatmaidInstance to retrieve annotations. Use neuron.get_annotations( remote_instance = CatmaidInstance )')
            return None
        elif not remote_instance:
            remote_instance = self._remote_instance

        self.neuron_name = pymaid.get_names(self.skeleton_id, remote_instance)[
            str(self.skeleton_id)]
        return self.neuron_name

    def resample(self, resample_to, inplace=True):
        """Resample the neuron to given resolution.

        Parameters
        ----------
        resample_to :           int
                                Resolution in nanometer to which to resample
                                the neuron.        
        inplace :               bool, optional
                                If True, operation will be performed on 
                                itself. If False, operation is performed on 
                                copy which is then returned.

        See Also
        --------
        :func:`~pymaid.resample_neuron`
            Base function. See for details and examples.

        """
        if inplace:
            x = self
        else:
            x = self.copy()

        morpho.resample_neuron(x, resample_to, inplace=True )

        # No need to call this as base function does this for us        
        # x._clear_temp_attr()

        if not inplace:
            return x

    def downsample(self, factor=5, preserve_cn_treenodes=True, inplace=True):
        """Downsample the neuron by given factor.

        Parameters
        ----------
        factor :                int, optional
                                Factor by which to downsample the neurons. 
                                Default = 5
        preserve_cn_treenodes : bool, optional
                                If True, treenodes that have connectors are 
                                preserved.
        inplace :               bool, optional
                                If True, operation will be performed on 
                                itself. If False, operation is performed on 
                                copy which is then returned.

        See Also
        --------
        :func:`~pymaid.downsample_neuron`
            Base function. See for details and examples.

        """
        if inplace:
            x = self
        else:
            x = self.copy()

        morpho.downsample_neuron(x, factor, inplace=True, preserve_cn_treenodes=preserve_cn_treenodes)

        # Delete outdated attributes
        x._clear_temp_attr()

        if not inplace:
            return x

    def reroot(self, new_root, inplace=True):
        """ Reroot neuron to given treenode ID or node tag.

        Parameters
        ----------
        new_root :  {int, str}
                    Either treenode ID or node tag
        inplace :   bool, optional
                    If True, operation will be performed on itself. If False, 
                    operation is performed on copy which is then returned.

        See Also
        --------
        :func:`~pymaid.reroot_neuron`
            Base function. See for details and examples.

        """

        if inplace:
            x = self
        else:
            x = self.copy()

        morpho.reroot_neuron(x, new_root, inplace=True)

        # Clear temporary attributes
        x._clear_temp_attr()   

        if not inplace:
            return x     

    def prune_distal_to(self, node, inplace=True):
        """Cut off nodes distal to given nodes.

        Parameters
        ----------
        node :      {treenode_id, node_tag}
                    Provide either treenode ID(s) or a unique tag(s)
        inplace :   bool, optional
                    If True, operation will be performed on itself. If False, 
                    operation is performed on copy which is then returned.

        See Also
        --------
        :func:`~pymaid.cut_neuron`
            Base function. See for details and examples.
        """

        if inplace:
            x = self
        else:
            x = self.copy()

        if not isinstance( node, (list, np.ndarray) ):                
            node = [node]

        for n in node:
            dist, prox = morpho.cut_neuron(x, n)
            x.__init__(prox, x._remote_instance, x._meta_data)

        # Clear temporary attributes
        x._clear_temp_attr()

        if not inplace:
            return x

    def prune_proximal_to(self, node, inplace=True):
        """Remove nodes proximal to given node. Reroots neuron to cut node.

        Parameters
        ----------
        node :      {treenode_id, node tag}
                    Provide either a treenode ID or a (unique) tag
        inplace :   bool, optional
                    If True, operation will be performed on itself. If False, 
                    operation is performed on copy which is then returned.

        See Also
        --------
        :func:`~pymaid.cut_neuron`
            Base function. See for details and examples.

        """

        if inplace:
            x = self
        else:
            x = self.copy()

        dist, prox = morpho.cut_neuron(x, node)
        x.__init__(dist, x._remote_instance, x._meta_data)

        # Clear temporary attributes
        x._clear_temp_attr()

        if not inplace:
            return x

    def prune_by_strahler(self, to_prune=range(1, 2), inplace=True):
        """ Prune neuron based on strahler order. Will reroot neuron to
        soma if possible.        

        Parameters
        ----------
        to_prune :  {int, list, range}, optional
                    Strahler indices to prune. 
                    1. ``to_prune = 1`` removes all leaf branches
                    2. ``to_prune = [1,2]`` removes indices 1 and 2
                    3. ``to_prune = range(1,4)`` removes indices 1, 2 and 3  
                    4. ``to_prune = -1`` removes everything but the highest index 
        inplace :   bool, optional
                    If True, operation will be performed on itself. If False, 
                    operation is performed on copy which is then returned.

        See Also
        --------
        :func:`~pymaid.prune_by_strahler` 
            This is the base function. See for details and examples.

        """     

        if inplace:
            x = self
        else:
            x = self.copy()   

        morpho.prune_by_strahler(
            x, to_prune=to_prune, reroot_soma=True, inplace=True)

        # No need to call this as morpho.prune_by_strahler does this already
        #self._clear_temp_attr()

        if not inplace:
            return x

    def prune_by_longest_neurite(self, reroot_to_soma=False, inplace=True):
        """ Prune neuron down to the longest neurite.

        Parameters
        ----------
        reroot_to_soma :    bool, optional
                            If True, will reroot to soma before pruning.
        inplace :           bool, optional
                            If True, operation will be performed on itself. 
                            If False, operation is performed on copy which is 
                            then returned.

        See Also
        --------
        :func:`~pymaid.longest_neurite`
            This is the base function. See for details and examples.

        """     

        if inplace:
            x = self
        else:
            x = self.copy()   

        morpho.longest_neurite(
            x, inplace=True, reroot_to_soma=reroot_to_soma)

        # Clear temporary attributes
        x._clear_temp_attr()

        if not inplace:
            return x

    def prune_by_volume(self, v, mode='IN', inplace=True):
        """ Prune neuron by intersection with given volume(s).

        Parameters
        ----------
        v :         {str, pymaid.Volume, list of either}
                    Volume(s) to check for intersection
        mode :      {'IN','OUT'}, optional
                    If 'IN', parts of the neuron inside the volume are kept.
        inplace :   bool, optional
                    If True, operation will be performed on itself. If False, 
                    operation is performed on copy which is then returned.

        See Also
        --------
        :func:`~pymaid.in_volume`
            Base function. See for details and examples.
        """ 

        if inplace:
            x = self
        else:
            x = self.copy()

        if not isinstance(v, Volume):            
            v = pymaid.get_volume(v, combine_vols=True, remote_instance=x._remote_instance)

        morpho.in_volume(x, v, inplace=True, remote_instance=x._remote_instance, mode=mode)

        # Clear temporary attributes
        x._clear_temp_attr()   

        if not inplace:
            return x     

    def reload(self, remote_instance=None):
        """Reload neuron from server. Currently only updates name, nodes, 
        connectors and tags."""

        if not remote_instance and not self._remote_instance:
            module_logger.error(
                'Get_update: Unable to connect to server. Please provide CatmaidInstance as <remote_instance>.')
        elif not remote_instance:
            remote_instance = self._remote_instance

        n = pymaid.get_neuron(
            self.skeleton_id, remote_instance=remote_instance)
        self.__init__(n, self._remote_instance, self._meta_data)

        # Clear temporary attributes
        self._clear_temp_attr()

    def set_remote_instance(self, remote_instance=None, server_url=None, http_user=None, http_pw=None, auth_token=None):
        """Assign remote_instance to neuron. Provide either existing 
        CatmaidInstance OR your credentials.

        Parameters
        ----------
        remote_instance :       pymaid.CatmaidInstance, optional
        server_url :            str, optional
        http_user :             str, optional
        http_pw :               str, optional
        auth_token :            str, optional

        """
        if remote_instance:
            self._remote_instance = remote_instance
        elif server_url and auth_token:
            self._remote_instance = pymaid.CatmaidInstance(server_url,
                                                           http_user,
                                                           http_pw,
                                                           auth_token
                                                           )
        else:
            raise Exception('Provide either CatmaidInstance or credentials.')

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return str(self.summary())

    def __add__(self, to_add):
        if isinstance(to_add, list):
            if False not in [isinstance(n, CatmaidNeuron) for n in to_add]:
                return CatmaidNeuronList(list(set([self] + to_add)))
            else:
                return CatmaidNeuronList(list(set([self] + [CatmaidNeuron[n] for n in to_add])))
        elif isinstance(to_add, CatmaidNeuron):
            return CatmaidNeuronList(list(set([self] + [to_add])))
        elif isinstance(to_add, CatmaidNeuronList):
            return CatmaidNeuronList(list(set([self] + to_add.neurons)))
        else:
            raise TypeError('Unable to add data of type {0}.'.format(
                              type(to_add)))            

    def summary(self):
        """Get a summary of this neuron."""

        # Look up these values without requesting them
        neuron_name = self.__dict__.get('neuron_name', 'NA')                
        review_status = self.__dict__.get('review_status', 'NA')        

        if 'nodes' in self.__dict__:
            soma_temp = self.soma
        else:
            soma_temp = 'NA'

        return pd.Series([type(self), neuron_name, self.skeleton_id, self.n_nodes, self.n_connectors, self.n_branch_nodes, self.n_end_nodes, self.n_open_ends, self.cable_length, review_status, soma_temp ],
                         index=['type', 'neuron_name', 'skeleton_id', 'n_nodes', 'n_connectors', 'n_branch_nodes', 'n_end_nodes',
                                'n_open_ends', 'cable_length', 'review_status', 'soma']
                         )

    @classmethod
    def from_swc(self, filename, neuron_name = None, neuron_id = None):
        """ Generate neuron object from SWC file.
        
        Parameters
        ----------
        filename :      str
                        Name of SWC file.
        neuronname :    str, optional
                        Name to use for the neuron. If not provided, will use
                        filename
        neuron_id :     int, optional
                        Unique identifier (essentially skeleton ID). If not 
                        provided, will generate one from scratch.

        Returns
        -------
        CatmaidNeuron

        """
        if not neuron_id:
            neuron_id = random.randint(100000,999999)

        if not neuron_name:
            neuron_name = filename

        data = []
        with open(filename) as file:
            reader = csv.reader(file, delimiter = ' ')
            for row in reader:
                #skip empty rows
                if not row:
                    continue
                #skip comments
                if not row[0].startswith('#'):
                    data.append(row)

        # Remove empty entries and generade nodes dataframe
        nodes = pd.DataFrame([ [ float(e) for e in row if e != '' ] for row in data ],
                            columns = ['treenode_id','label','x','y','z','radius','parent_id'], dtype=object )

        # Bring from um into nm space
        nodes[['x','y','z','radius']] *= 1000

        connectors = pd.DataFrame([], columns = ['treenode_id', 'connector_id', 'relation', 'x', 'y', 'z'], dtype=object )

        df = pd.DataFrame([[
            neuron_name,
            str(neuron_id),
            nodes,
            connectors,
            {},            
            ]],
                columns=['neuron_name', 'skeleton_id',
                         'nodes', 'connectors', 'tags'],
                dtype=object
            )

        # Placeholder for igraph representations of neurons
        df['igraph'] = None

        return CatmaidNeuron(df)

class CatmaidNeuronList:
    """ Compilations of :class:`~pymaid.CatmaidNeuron` that allow quick 
    access to neurons' attributes/functions. They are designed to work in many 
    ways much like a pandas.DataFrames by, for example, supporting ``.ix[ ]``, 
    ``.itertuples()``, ``.empty`` or ``.copy()``.  

    Notes
    -----
    CatmaidNeuronList can be minimally constructed from just skeleton IDs. 
    Other parameters (nodes, connectors, neuron name, annotations, etc.) 
    will then be retrieved from the server 'on-demand'. 

    The easiest way to get a CatmaidNeuronList is by using 
    :func:`~pymaid.get_neuron` (see examples).

    Manually, a CatmaidNeuronList can constructed from a pandas DataFrame (df)
    containing: df.nodes, df.connectors, df.skeleton_id, df.neuron_name, 
    df.tags for a set of neurons.

    Parameters
    ----------
    x                 
                        Data to construct neuron from. Can be either:

                        1. skeleton ID(s)                        
                        2. CatmaidNeuronList (will create a deep copy)
                        3. pandas DataFrame
    remote_instance :   CatmaidInstance, optional
                        Storing this makes it more convenient to retrieve e.g. 
                        neuron annotations, review status, etc.
    meta_data :         dict, optional
                        Any additional data
    make_copy :         boolean, optional
                        If true, DataFrames are copied [.copy()] before being 
                        assigned to the neuron object to prevent 
                        backpropagation of subsequent changes to the data. 
                        Default = True   

    Attributes
    ----------
    skeleton_id :       np.array of str                        
    neuron_name :       np.array of str                        
    nodes :             ``pandas.DataFrame``
                        Merged treenode table
    connectors :        ``pandas.DataFrame``
                        Merged connector table    
    tags :              np.array of dict
                        Treenode tags
    annotations :       np.array of list                        
    igraph :            np.array of ``iGraph`` objects                        
    review_status :     np.array of int                        
    n_connectors :      np.array of int                        
    n_presynapses :     np.array of int                        
    n_postsynapses :    np.array of int                        
    n_branch_nodes :    np.array of int                        
    n_end_nodes :       np.array of int                        
    n_open_ends :       np.array of int                        
    cable_length :      np.array of float 
                        Cable lengths in micrometers [um]    
    soma :              np.array of treenode_ids                        
    root :              np.array of treenode_ids
    n_cores :           int
                        Number of cores to use. Default is os.cpu_count()-1
    _use_parallel :     bool (default=True)
                        If True, will use parallel threads. Should be slightly
                        up to a lot faster depending of the numbers of cores.
                        Switch off if you experience performance issues.

    Examples
    --------
    >>> # Initialize with just a Skeleton ID 
    >>> nl = pymaid.CatmaidNeuronList( [ 123456, 45677 ] )
    >>> # Add CatmaidInstance to neurons in neuronlist
    >>> rm = pymaid.CatmaidInstance(server_url, http_user, http_pw, token)
    >>> nl.set_remote_instance( rm )
    >>> # Retrieve review status from server on-demand
    >>> nl.review_status
    ... array([ 90, 23 ])
    >>> # Initialize with skeleton data
    >>> nl = pymaid.get_neuron( [ 123456, 45677 ] )
    >>> # Get annotations from server
    >>> nl.annotations
    ... [ ['annotation1','annotation2'],['annotation3','annotation4'] ]
    >>> Index using node count
    >>> subset = nl [ nl.n_nodes > 6000 ]
    >>> # Get neuron by its skeleton ID
    >>> n = nl.skid[ 123456 ]
    >>> # Index by multiple skeleton ID
    >>> subset = nl [ [ '123456', '45677' ] ]
    >>> # Index by neuron name
    >>> subset = nl [ 'name1' ]
    >>> # Index using annotation
    >>> subset = nl ['annotation:uPN right']
    >>> # Concatenate lists
    >>> nl += pymaid.get_neuron( [ 912345 ], remote_instance = rm )

    """

    def __init__(self, x, remote_instance=None, make_copy=True, _use_parallel=False):
        # Set number of cores
        self.n_cores = max(1, os.cpu_count())

        # If below parameter is True, most calculations will be parallelized
        # which speeds them up quite a bit. Unfortunately, this uses A TON of 
        # memory - for large lists this might make your system run out of 
        # memory. In these cases, leave this property at False
        self._use_parallel = _use_parallel
        self._use_threading = True

        # Determines if subsetting this NeuronList will return copies
        self.copy_on_subset = False

        if remote_instance is None:
            try:
                remote_instance = x.remote_instance 
            except:
                if 'remote_instance' in sys.modules:
                    remote_instance = sys.modules['remote_instance']
                elif 'remote_instance' in globals():
                    remote_instance = globals()['remote_instance']

        if not isinstance(x, (list, pd.DataFrame, CatmaidNeuronList, np.ndarray)):
            self.neurons = list([x])
        elif isinstance(x, pd.DataFrame):
            self.neurons = [x.loc[i] for i in range(x.shape[0])]
        elif isinstance(x, CatmaidNeuronList):
            # This has to be made a copy otherwise changes in the list will
            # backpropagate
            self.neurons = [n for n in x.neurons]
        elif isinstance(x, (list, np.ndarray)):
            # If x is a list of mixed objects we need to unpack/flatten that
            # E.g. x = [CatmaidNeuronList, CatmaidNeuronList, CatmaidNeuron, skeletonID ]

            to_unpack = [e for e in x if isinstance(e, CatmaidNeuronList)]
            x = [e for e in x if not isinstance(e, CatmaidNeuronList)]
            x += [n for ob in to_unpack for n in ob.neurons]

            # We have to convert from numpy ndarray to list - do NOT remove
            # list() here
            self.neurons = list(x)
        else:
            raise TypeError(
                'Unable to generate CatmaidNeuronList from %s' % str(type(x)))                
        
        # Now convert into CatmaidNeurons if necessary
        to_convert = []
        for i, n in enumerate(self.neurons):
            if not isinstance(n, CatmaidNeuron) or make_copy is True:
                to_convert.append( (n,remote_instance,i) )

        if to_convert:
            if self._use_threading:
                with ThreadPoolExecutor(max_workers=self.n_cores) as e:

                    futures = e.map( CatmaidNeuron, [ n[0] for n in to_convert ]  )

                    converted = [ n for n in tqdm(futures, total=len(to_convert), 
                                                  desc='Making neurons', 
                                                  disable=module_logger.getEffectiveLevel()>=40 ) ]

                    for i, c in enumerate(to_convert):
                        self.neurons[ c[2] ] = converted[ i ]

            else:
                for n in tqdm(to_convert, desc='Making neurons', disable=module_logger.getEffectiveLevel()>=40):
                    self.neurons[ n[2] ] = CatmaidNeuron(
                            n[0], remote_instance=remote_instance)

        # Add indexer class
        self.ix = _IXIndexer(self.neurons, module_logger)

        # Add skeleton ID indexer class
        self.skid = _SkidIndexer(self.neurons, module_logger)

    def _convert_helper(self, x):
        """ Helper function to convert x to CatmaidNeuron."""
        return CatmaidNeuron( x[0], remote_instance=x[1])   

    def summary(self, n=None):
        """ Get summary over all neurons in this NeuronList.

        Parameters
        ----------
        n :     int, optional
                Get only first N entries

        Returns
        -------
        pandas DataFrame  

        """
        d = []
        for n in self.neurons[:n]:
            neuron_name = n.__dict__.get('neuron_name', 'NA')            
            review_status = n.__dict__.get('review_status', 'NA')

            if 'nodes' in n.__dict__:
                soma_temp = n.soma != None
            else:
                soma_temp = 'NA'

            d.append([neuron_name, n.skeleton_id, n.n_nodes, n.n_connectors, n.n_branch_nodes, n.n_end_nodes, n.n_open_ends,
                      n.cable_length, review_status, soma_temp ])

        return pd.DataFrame(data=d,
                            columns=['neuron_name', 'skeleton_id', 'n_nodes', 'n_connectors', 'n_branch_nodes', 'n_end_nodes', 'open_ends',
                                     'cable_length', 'review_status', 'soma' ]
                            )
    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return '{0} of {1} neurons \n {2}'.format(type(self), len(self.neurons), str(self.summary()) )    

    def __iter__(self):
        """ Iterator instanciates a new class everytime it is called. 
        This allows the use of nested loops on the same neuronlist object.
        """
        class prange_iter:
            def __init__(self, neurons, start):
                self.iter = start
                self.neurons = neurons

            def __next__(self):
                if self.iter >= len(self.neurons):
                    raise StopIteration
                to_return = self.neurons[self.iter]
                self.iter += 1
                return to_return

        return prange_iter(self.neurons,0)

    def __len__(self):
        return len(self.neurons)

    def __getattr__(self, key):
        if key == 'shape':
            return (self.__len__(),)
        elif key in ['n_nodes','n_connectors','n_presynapses','n_postsynapses',
                     'n_open_ends','n_end_nodes','cable_length','tags','igraph',
                     'soma','root','segments', 'igraph','n_branch_nodes','dps']:
            self.get_skeletons(skip_existing=True)
            return np.array([ getattr(n,key) for n in self.neurons ])
        elif key == 'neuron_name':
            self.get_names(skip_existing=True)
            return np.array([n.neuron_name for n in self.neurons])
        elif key == 'skeleton_id':
            return np.array([n.skeleton_id for n in self.neurons])        
        elif key in ['nodes','connectors','presynapses','postsynapses','gap_junctions']:
            self.get_skeletons(skip_existing=True)
            return pd.concat([ getattr(n, key) for n in self.neurons], axis=0, ignore_index=True)
        elif key == '_remote_instance':
            all_instances = [
                n._remote_instance for n in self.neurons if n._remote_instance != None]            

            if len(set(all_instances)) > 1:
                # Note that multiprocessing causes remote_instances to be pickled
                # and thus not be the same anymore
                module_logger.debug(
                    'Neurons are using multiple remote_instances! Returning first entry.')
            elif len(set(all_instances)) == 0:
                raise Exception(
                    'No remote_instance found. Use .set_remote_instance() to assign one to all neurons.')
            else:
                return all_instances[0]        
        elif key == 'review_status':
            self.get_review(skip_existing=True)
            return np.array([n.review_status for n in self.neurons])
        elif key == 'annotations':
            to_retrieve = [
                n.skeleton_id for n in self.neurons if 'annotations' not in n.__dict__]
            if to_retrieve:
                re = pymaid.get_annotations(
                    to_retrieve, remote_instance=self._remote_instance)
                for n in [n for n in self.neurons if 'annotations' not in n.__dict__]:
                    n.annotations = re[str(n.skeleton_id)]
            return np.array([n.annotations for n in self.neurons])
        elif key == 'empty':
            return len(self.neurons) == 0
        else:
            raise AttributeError('Attribute "%s" not found' % key)

    def __contains__(self, x):
        return x in self.neurons or str(x) in self.skeleton_id or x in self.neuron_name

    def __getitem__(self, key):
        if isinstance(key, str):
            if key.startswith('annotation:'):
                skids = pymaid.eval_skids(
                    key, remote_instance=self._remote_instance)
                subset = self[skids]
            else:
                subset = [
                    n for n in self.neurons if key in n.neuron_name or key in n.skeleton_id]
        elif isinstance(key, list):
            if True in [isinstance(k, str) for k in key]:
                subset = [n for i, n in enumerate(self.neurons) if True in [
                    k == n.neuron_name for k in key] or True in [k == n.skeleton_id for k in key]]
            elif False not in [isinstance(k, bool) for k in key]:
                subset = [n for i, n in enumerate(self.neurons) if key[i]]
            else:
                subset = [self.neurons[i] for i in key]
        elif isinstance(key, np.ndarray) and key.dtype == 'bool':
            subset = [n for i, n in enumerate(self.neurons) if key[i]]
        else:
            subset = self.neurons[key]

        if isinstance(subset, CatmaidNeuron):
            return subset

        return CatmaidNeuronList(subset, make_copy=self.copy_on_subset)

    def __missing__(self, key):
        module_logger.error('No neuron matching the search critera.')
        raise AttributeError('No neuron matching the search critera.')

    def __add__(self, to_add):
        if isinstance(to_add, list):
            if False not in [isinstance(n, CatmaidNeuron) for n in to_add]:
                return CatmaidNeuronList(list(set(self.neurons + to_add)))
            else:
                return CatmaidNeuronList(list(set(self.neurons + [CatmaidNeuron[n] for n in to_add])))
        elif isinstance(to_add, CatmaidNeuron):
            return CatmaidNeuronList(list(set(self.neurons + [to_add])))
        elif isinstance(to_add, CatmaidNeuronList):
            return CatmaidNeuronList(list(set(self.neurons + to_add.neurons)))
        else:
            module_logger.error('Unable to add data of type %s.' %
                              str(type(to_add)))

    def __sub__(self, to_sub):
        if isinstance(to_sub, str) or isinstance(to_sub, int):
            return CatmaidNeuronList([n for n in self.neurons if n.skeleton_id != to_sub and n.neuron_name != to_sub])
        elif isinstance(to_sub, list):
            return CatmaidNeuronList([n for n in self.neurons if n.skeleton_id not in to_sub and n.neuron_name not in to_sub])
        elif isinstance(to_sub, CatmaidNeuron):
            return CatmaidNeuronList([n for n in self.neurons if n != to_sub])
        elif isinstance(to_sub, CatmaidNeuronList):
            return CatmaidNeuronList([n for n in self.neurons if n not in to_sub])

    def sum(self):
        """Returns sum numeric and boolean values over all neurons. """
        return self.summary().sum(numeric_only=True)

    def mean(self):
        """Returns mean numeric and boolean values over all neurons. """
        return self.summary().mean(numeric_only=True)

    def sample(self, N=1):
        """Returns random subset of neurons."""
        indices = list(range(len(self.neurons)))
        random.shuffle(indices)
        return CatmaidNeuronList([n for i, n in enumerate(self.neurons) if i in indices[:N]])

    def resample(self, resample_to, preserve_cn_treenodes=False, inplace=True):
        """Resamples all neurons to given resolution.

        Parameters
        ----------
        resample_to :           int
                                Resolution in nm to resample neurons to.
        inplace :               bool, optional
                                If False, a downsampled COPY of this 
                                CatmaidNeuronList is returned.

        See Also
        --------
        :func:`~pymaid.resample_neuron`
                Base function - see for details.
        """                

        x = self
        if not inplace:
            x = x.copy() 

        _set_loggers('ERROR')

        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,resample_to) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._resample_helper, combinations, chunksize=10 ), total=len(combinations), desc='Downsampling', disable=module_logger.getEffectiveLevel()>=40 ))

            pool.close()
            pool.join()  
        else:
            for n in tqdm(x.neurons, desc='Resampling', disable=module_logger.getEffectiveLevel()>=40):
                n.resample(resample_to=resample_to, inplace=True)
            
        _set_loggers('INFO')

        if not inplace:
            return x

    def _resample_helper(self, x):
        """ Helper function to parallelise basic operations."""    
        x[0].resample(resample_to=x[1],inplace=True)
        return x[0]

    def downsample(self, factor=5, preserve_cn_treenodes=False, inplace=True):
        """Downsamples (simplifies) all neurons by given factor.

        Parameters
        ----------
        factor :                int, optional
                                Factor by which to downsample the neurons. 
                                Default = 5
        preserve_cn_treenodes : bool, optional
                                If True, treenodes that have connectors are 
                                preserved.
        inplace :               bool, optional
                                If False, a downsampled COPY of this 
                                CatmaidNeuronList is returned.

        See Also
        --------
        :func:`~pymaid.downsample_neuron`
                Base function - see for details.
        """                

        x = self
        if not inplace:
            x = x.copy() 

        _set_loggers('ERROR')

        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,factor,preserve_cn_treenodes) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._downsample_helper, combinations, chunksize=10 ), total=len(combinations), desc='Downsampling', disable=module_logger.getEffectiveLevel()>=40 ))

            pool.close()
            pool.join()  
        else:
            for n in tqdm(x.neurons, desc='Downsampling', disable=module_logger.getEffectiveLevel()>=40):
                n.downsample(factor=factor, preserve_cn_treenodes=preserve_cn_treenodes, inplace=True)
            
        _set_loggers('INFO')

        if not inplace:
            return x

    def _downsample_helper(self, x):
        """ Helper function to parallelise basic operations."""    
        x[0].downsample(factor=x[1],preserve_cn_treenodes=preserve_cn_treenodes,inplace=True)                
        return x[0]

    def reroot(self, new_root, inplace=True):
        """ Reroot neuron to treenode ID or node tag.

        Parameters
        ----------
        new_root :  {int, str, list of either}
                    Either treenode IDs or node tag(s). If not a list, the
                    same tag is used to reroot all neurons
        inplace :   bool, optional
                    If False, a rerooted COPY of this CatmaidNeuronList is 
                    returned

        See Also
        --------
        :func:`~pymaid.reroot_neuron`
                    Base function. See for details and more examples.

        Examples
        --------
        >>> # Reroot all neurons to soma
        >>> nl = pymaid.get_neuron('annotation:glomerulus DA1')
        >>> nl.reroot( nl.soma )
        """       

        if not isinstance(new_root, (list,np.ndarray)):
            new_root = [new_root] * len(self.neurons)

        if len(new_root) != len(self.neurons):
            raise ValueError('Must provided a new root for every neuron.')

        x = self
        if not inplace:
            x = x.copy() 

        # Silence loggers (except Errors)
        morpholevel = morpho.module_logger.getEffectiveLevel()
        igraphlevel = igraph_catmaid.module_logger.getEffectiveLevel()
        igraph_catmaid.module_logger.setLevel('ERROR')
        morpho.module_logger.setLevel('ERROR')
        
        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,new_root[i]) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._reroot_helper, combinations, chunksize=10 ), total=len(combinations), desc='Rerooting', disable=module_logger.getEffectiveLevel()>=40 ))

            pool.close()
            pool.join()
        else:
            for i, n in enumerate( tqdm(x.neurons, desc='Rerooting', disable=module_logger.getEffectiveLevel()>=40) ):
                n.reroot( new_root[i], inplace=True )

        # Reset logger level to previous state
        igraph_catmaid.module_logger.setLevel(igraphlevel)
        morpho.module_logger.setLevel(morpholevel)

        if not inplace:
            return x

    def _reroot_helper(self, x):
        """ Helper function to parallelise basic operations."""    
        x[0].reroot(x[1], inplace=True)                
        return x[0]

    def prune_distal_to(self, tag, inplace=True):
        """Cut off nodes distal to given node. 

        Parameters
        ----------
        node :      node tag
                    A (unique) tag at which to cut the neurons
        inplace :   bool, optional
                    If False, a pruned COPY of this CatmaidNeuronList is 
                    returned.

        """
        x = self
        if not inplace:
            x = x.copy() 

        _set_loggers('ERROR')

        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,tag) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._prune_distal_helper, combinations, chunksize=10 ), total=len(combinations), desc='Pruning', disable=module_logger.getEffectiveLevel()>=40 ))

            pool.close()
            pool.join()   
        else:
            for n in tqdm(x.neurons, desc='Pruning', disable=module_logger.getEffectiveLevel()>=40):
                n.prune_distal_to( tag, inplace=True )

        _set_loggers('INFO')   

        if not inplace:
            return x     

    def _prune_distal_helper(self, x):
        """ Helper function to parallelise basic operations."""    
        x[0].prune_distal_to(x[1], inplace=True)                
        return x[0]

    def prune_proximal_to(self, tag, inplace=True):
        """Remove nodes proximal to given node. Reroots neurons to cut node.

        Parameters
        ----------
        node :      node tag
                    A (unique) tag at which to cut the neurons
        inplace :   bool, optional
                    If False, a pruned COPY of this CatmaidNeuronList is 
                    returned.

        """
        x = self
        if not inplace:
            x = x.copy() 

        _set_loggers('ERROR')

        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,tag) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._prune_proximal_helper, combinations, chunksize=10 ), total=len(combinations), desc='Pruning', disable=module_logger.getEffectiveLevel()>=40 ))

            pool.close()
            pool.join() 
        else:
            for n in tqdm(x.neurons, desc='Pruning', disable=module_logger.getEffectiveLevel()>=40):
                n.prune_proximal_to(tag, inplace=True)

        _set_loggers('INFO')        

        if not inplace:
            return x

    def _prune_proximal_helper(self, x):
        """ Helper function to parallelise basic operations."""    
        x[0].prune_proximal_to(x[1], inplace=True)                
        return x[0]

    def prune_by_strahler(self, to_prune=range(1, 2), inplace=True):
        """ Prune neurons based on strahler order. Will reroot neurons to
        soma if possible.

        Parameters
        ----------
        to_prune :  {int, list, range}, optional
                    Strahler indices to prune. 
                    1. ``to_prune = 1`` removes all leaf branches
                    2. ``to_prune = [1,2]`` removes indices 1 and 2
                    3. ``to_prune = range(1,4)`` removes indices 1, 2 and 3  
                    4. ``to_prune = -1`` removes everything but the highest index 
        inplace :   bool, optional
                    If False, a pruned COPY of this CatmaidNeuronList is 
                    returned.

        See Also
        --------
        :func:`pymaid.prune_by_strahler`
                    Basefunction - see for details and examples. 

        """
        x = self
        if not inplace:
            x = x.copy() 

        _set_loggers('ERROR')

        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,to_prune) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._prune_strahler_helper, combinations, chunksize=10 ), total=len(combinations), desc='Pruning', disable=module_logger.getEffectiveLevel()>=40 ))

            pool.close()
            pool.join()
        else:
            for n in tqdm(x.neurons, desc='Pruning', disable=module_logger.getEffectiveLevel()>=40):
                n.prune_by_strahler(to_prune=to_prune, inplace=True)

        _set_loggers('INFO')

        if not inplace:
            return x

    def _prune_strahler_helper(self, x):
        """ Helper function to parallelise basic operations."""    
        x[0].prune_by_strahler(to_prune=x[1], inplace=True)                
        return x[0]

    def prune_by_longest_neurite(self, reroot_to_soma=False, inplace=True):
        """ Prune neurons down to their longest neurites.

        Parameters
        ----------
        reroot_to_soma :    bool, optional
                            If True, neurons will be rerooted to their somas 
                            before pruning.
        inplace :           bool, optional
                            If False, a pruned COPY of this CatmaidNeuronList 
                            is returned.

        See Also
        --------
        :func:`pymaid.prune_by_strahler`
                        Basefunction - see for details and examples.
        """

        x = self
        if not inplace:
            x = x.copy() 

        _set_loggers('ERROR')

        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,reroot_to_soma) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._prune_neurite_helper, combinations, chunksize=10 ), total=len(combinations), desc='Pruning', disable=module_logger.getEffectiveLevel()>=40 ))

            pool.close()
            pool.join()
        else:
            for n in tqdm(x.neurons, desc='Pruning', disable=module_logger.getEffectiveLevel()>=40):
                n.prune_by_longest_neurite(reroot_to_soma=reroot_to_soma, inplace=True)

        _set_loggers('INFO')

        if not inplace:
            return x

    def _prune_neurite_helper(self, x):
        """ Helper function to parallelise basic operations."""    
        x[0].prune_by_longest_neurite(x[1], inplace=True)                
        return x[0]

    def prune_by_volume(self, v, mode='IN', inplace=True):
        """ Prune neuron by intersection with given volume(s).

        Parameters
        ----------
        v :         {str, pymaid.Volume, list of either}
                    Volume(s) to check for intersection.
        mode :      {'IN','OUT'}, optional
                    If 'IN', part of the neuron inside the volume(s) is kept.
        inplace :   bool, optional
                    If False, a pruned COPY of this CatmaidNeuronList is 
                    returned.


        See Also
        --------
        :func:`~pymaid.in_volume`
                Basefunction - see for details and examples.
        """ 
        
        x = self
        if not inplace:
            x = x.copy()       
        
        if not isinstance(v, Volume):            
            v = pymaid.get_volume(v, combine_vols=True) 

        _set_loggers('ERROR')

        if x._use_parallel:
            pool = mp.Pool(x.n_cores)
            combinations = [ (n,v,mode) for i,n in enumerate(x.neurons) ]   
            x.neurons = list(tqdm( pool.imap( x._prune_by_volume_helper, combinations, chunksize=10 ), total=len(combinations), desc='Pruning' , disable=module_logger.getEffectiveLevel()>=40))

            pool.close()
            pool.join()
        else:
            for n in tqdm(x.neurons, desc='Pruning', disable=module_logger.getEffectiveLevel()>=40):
                n.prune_by_volume(v, mode=mode, inplace=True)

        _set_loggers('INFO')

        if not inplace:
            return x

    def _prune_by_volume_helper(self, x):
        """ Helper function to parallelise basic operations."""
        x[0].prune_by_volume(x[1],mode=x[2], inplace=True)
        return x[0]

    def get_review(self, skip_existing=False):
        """ Use to get/update review status."""
        if skip_existing:
            to_update = [
                n.skeleton_id for n in self.neurons if 'review_status' not in n.__dict__]
        else:
            to_update = self.skeleton_id.tolist()

        if to_update:
            re = pymaid.get_review(
                to_update, remote_instance=self._remote_instance).set_index('skeleton_id')
            for n in self.neurons:
                if str(n.skeleton_id) in re:
                    n.review_status = re.ix[str(n.skeleton_id)].percent_reviewed            

    def get_annotations(self, skip_existing=False):
        """Get/update annotations for neurons."""     
        if skip_existing:
            to_update = [
                n.skeleton_id for n in self.neurons if 'annotations' not in n.__dict__]
        else:
            to_update = self.skeleton_id.tolist()

        if to_update:
            annotations = pymaid.get_annotations( to_update, remote_instance=self._remote_instance )
            for n in self.neurons:
                if str(n.skeleton_id) in annotations: 
                    n.annotations = annotations[ str(n.skeleton_id) ]

    def get_names(self, skip_existing=False):
        """ Use to get/update neuron names."""
        if skip_existing:
            to_update = [
                n.skeleton_id for n in self.neurons if 'neuron_name' not in n.__dict__]
        else:
            to_update = self.skeleton_id.tolist()

        if to_update:
            names = pymaid.get_names(
                self.skeleton_id, remote_instance=self._remote_instance)
            for n in self.neurons:
                if str(n.skeleton_id) in names:
                    n.neuron_name = names[str(n.skeleton_id)]                

    def _generate_segments(self):
        """ Helper function to use multiprocessing to generate segments for all
        neurons. This will NOT force update of existing segments! This is about
        1.5X faster than calling them individually on a 4 core system.
        """

        if self._use_parallel:
            to_retrieve = [ n for n in self.neurons if 'segments' not in n.__dict__ ]
            to_retrieve_ix = [ i for i,n in enumerate(self.neurons) if 'segments' not in n.__dict__ ]

            pool = mp.Pool(self.n_cores)        
            update = list(tqdm( pool.imap( self._generate_segments_helper, to_retrieve, chunksize=10 ), total=len(to_retrieve), desc='Gen. segments', disable=module_logger.getEffectiveLevel()>=40 ))
            pool.close()
            pool.join()        

            for ix,n in zip(to_retrieve_ix,update):
                self.neurons[ ix ] = n
        else:
            for n in tqdm(self.neurons, desc='Gen. segments', disable=module_logger.getEffectiveLevel()>=40):
                if 'segments' not in n.__dict__:
                    _ = n.segments

    def _generate_segments_helper(self, x):
        """ Helper function to parallelise basic operations."""   
        if 'segments' not in x.__dict__:         
            _ = x.segments        
        return x      

    def reload(self):
        """ Update neuron skeletons."""
        self.get_skeletons(skip_existing=False)

    def get_skeletons(self, skip_existing=False):
        """Helper function to fill in/update skeleton data of neurons.         
        Updates ``.nodes``, ``.connectors``, ``.tags``, ``.date_retrieved`` and 
        ``.neuron_name``. Will also generate new igraph representation to match 
        nodes/connectors.
        """

        if skip_existing:
            to_update = [n for n in self.neurons if 'nodes' not in n.__dict__]
        else:
            to_update = self.neurons

        if to_update:
            skdata = pymaid.get_neuron(
                [n.skeleton_id for n in to_update], remote_instance=self._remote_instance, return_df=True).set_index('skeleton_id')
            for n in tqdm(to_update, desc='Processing neurons', disable=module_logger.getEffectiveLevel()>=40):                

                n.nodes = skdata.loc[str(n.skeleton_id),'nodes']
                n.connectors = skdata.loc[str(n.skeleton_id),'connectors']
                n.tags = skdata.loc[str(n.skeleton_id),'tags']
                n.neuron_name = skdata.loc[str(n.skeleton_id),'neuron_name']
                n.date_retrieved = datetime.datetime.now().isoformat()

                if 'type' not in n.nodes:
                    morpho.classify_nodes(n)

                # Delete outdated attributes
                n._clear_temp_attr()

    def set_remote_instance(self, remote_instance=None, server_url=None, http_user=None, http_pw=None, auth_token=None):
        """Assign remote_instance to all neurons. Provide either existing 
        CatmaidInstance OR your credentials.

        Parameters
        ----------
        remote_instance :       pymaid.CatmaidInstance, optional
        server_url :            str, optional
        http_user :             str, optional
        http_pw :               str, optional
        auth_token :            str, optional

        """

        if not remote_instance and server_url and auth_token:
            remote_instance = pymaid.CatmaidInstance(server_url,
                                                     http_user,
                                                     http_pw,
                                                     auth_token
                                                     )
        elif not remote_instance:
            raise Exception('Provide either CatmaidInstance or credentials.')

        for n in self.neurons:
            n._remote_instance = remote_instance

    def plot3d(self, **kwargs):
        """Plot neuron in 3D.   

        Parameters
        ---------
        **kwargs
                Keyword arguments will be passed to plot3d().
                See ``help(pymaid.plot3d)`` for a list of keywords.     

        See Also
        --------
        :func:`~pymaid.plot3d` 
                Base function called to generate 3d plot.               
        """

        if 'remote_instance' not in kwargs:
            kwargs.update({'remote_instance': self._remote_instance})

        self.get_skeletons(skip_existing=True)
        return plotting.plot3d(self, **kwargs)

    def plot2d(self, **kwargs):
        """Plot neuron in 2D.

        Parameters
        ----------
        **kwargs        
                Keyword arguments will be passed to plot2d(). See 
                ``help(pymaid.plot3d)`` for a list of accepted keywords.

        See Also
        --------
        :func:`~pymaid.plot2d` 
                Base function called to generate 2d plot                      
        """
        self.get_skeletons(skip_existing=True)
        return plotting.plot2d(self, **kwargs)

    def has_annotation(self, x, intersect=False ):
        """Filter neurons by their annotations.

        Parameters
        ----------
        x :             {str, list of str}
                        Annotation(s) to filter for
        intersect :     bool, optional
                        If True, neuron must have ALL provided annotations.

        Returns
        -------
        :class:`pymaid.CatmaidNeuronList`
                    Neurons that have given annotation(s).                  
        """ 

        if not isinstance(x, (list, np.ndarray)):
            x = [ x ]

        if not intersect:
            selection = [ self.neurons[i] for i, an in enumerate( self.annotations ) if True in [ a in x for a in an ] ]
        else:
            selection = [ self.neurons[i] for i, an in enumerate( self.annotations ) if False not in [ a in x for a in an ] ]

        if not selection:
            raise ValueError('No neurons with matching annotation(s) found')
        else:
            return CatmaidNeuronList(selection, make_copy=self.copy_on_subset)

    @classmethod
    def from_json(self, fname):
        """ Generates NeuronList from jsonfile
        """

        # Read data from file
        with open(fname, 'r') as f:
            data = json.load(f)

        # Generate NeuronLost
        nl = CatmaidNeuronList( [ e['skeleton_id'] for e in data ] )

        # Add colors
        for e in data:
            nl.skid[ e['skeleton_id'] ].color = tuple( int(e['color'].lstrip('#')[i:i+2], 16) for i in (0,2,4) )

        return nl

    def to_json(self, fname='selection.json'):
        """Saves neuron selection as json file which can be loaded
        in CATMAID selection table. Uses neuron's ``.color`` attribute.

        Parameters
        ----------
        fname :     str, optional
                    Filename to save selection to
        """

        data = [dict(skeleton_id=int(n.skeleton_id),
                     color="#{:02x}{:02x}{:02x}".format( n.color[0],n.color[1],n.color[2] ),
                     opacity=1
                     ) for n in self.neurons]

        with open(fname, 'w') as outfile:
            json.dump(data, outfile)

        module_logger.info('Selection saved as %s in %s' % (fname, os.getcwd()))    

    def itertuples(self):
        """Helper class to mimic ``pandas.DataFrame`` ``itertuples()``."""
        return self.neurons

    def sort_values(self, key, ascending=False):
        """Sort neurons by given value. See .summary() for valid keys."""
        summary = self.summary().sort_values(key).reset_index(drop=True)
        new_order = { s : i for i, s in enumerate(summary.skeleton_id.tolist()) }
        self.neurons = sorted( self.neurons, 
                               key = lambda x : new_order[x.skeleton_id], 
                               reverse= ascending==False )

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self):
        return self.copy()

    def copy(self):
        """Return copy of this CatmaidNeuronList."""
        return CatmaidNeuronList(self, make_copy=True, _use_parallel=self._use_parallel)

    def head(self, n=5):
        """Return summary for top N neurons."""
        return self.summary(n=n)


def _set_loggers(level='ERROR'):
    """Helper function to set levels for all associated module loggers."""
    morpho.module_logger.setLevel(level)
    igraph_catmaid.module_logger.setLevel(level)
    plotting.module_logger.setLevel(level)


class _IXIndexer():
    """ Location based indexer added to CatmaidNeuronList objects to allow
    indexing similar to pandas DataFrames using df.ix[0]. This is really 
    just a helper to allow code to operate on CatmaidNeuron the same way
    it would on DataFrames.
    """

    def __init__(self, obj, logger=None):
        self.obj = obj
        module_logger = logger

    def __getitem__(self, key):
        if isinstance(key, int) or isinstance(key, slice):
            return self.obj[key]
        else:
            raise Exception('Unable to index non-integers.')

class _SkidIndexer():
    """ Skeleton ID based indexer added to CatmaidNeuronList objects to allow
    indexing. This allows you to get a neuron by its skeleton ID.
    """

    def __init__(self, obj, logger=None):
        self.obj = obj
        module_logger = logger

    def __getitem__(self, skid):
        try:
            int(skid)
        except:
            raise Exception('Can only index skeleton IDs')

        sel = [n for n in self.obj if str(n.skeleton_id) == str(skid) ]

        if len( sel ) == 0:
            raise ValueError('No neuron with skeleton ID {0}'.format(skid))
        else:
            return sel[0]            

class Dotprops(pd.DataFrame):
    """ Class to hold dotprops. This is essentially a pandas DataFrame - we
    just use it to tell dotprops from other objects.

    See Also
    --------
    :func:`pymaid.rmaid.dotprops2py`
        Converts R dotprops to core.Dotprops

    Notes
    -----
    This class is still in the making but the idea is to write methods for it
    like .plot3d(), .to_X().

    """


class Volume(dict):
    """ Class to hold CATMAID meshes. This is essentially a dictionary with a 
    few additional perks (see below). 

    Important
    ---------
    Due to this being a subclass of ``dict``, using 
    ``isinstance(Volume, dict)`` will return ``True``!

    See Also
    --------
    :func:`~pymaid.get_volume`
        Retrieves volumes from CATMAID and returns :class:`pymaid.Volume`

    Notes
    -----
    This class is still in the making but the idea is to write methods for it
    like .to_X(), .get_neurons().

    Attributes could be: .volume, .bbox, .color
    """

    @classmethod
    def combine(self, x, name='comb_vol', color=(120, 120, 120, .6)):
        """ Merges multiple volumes into a single object.

        Parameters
        ----------
        x :     list or dict of volumes
        name :  str, optional
                Name of the combined volume
        color : tuple, optional
                Color of the combined volume

        Returns
        -------
        volume
        """

        if isinstance(x, Volume):
            return x

        if isinstance(x, dict):
            x = list(x.values())

        if not isinstance(x, list):
            raise TypeError('x must be list of volumes')
        elif False in [ isinstance(v, Volume) for v in x ]:
            raise TypeError('x must be list of volumes')

        vertices = []
        faces = []

        # Reindex faces
        for vol in x:
            offs = len(vertices)
            vertices += vol['vertices']
            faces += [ [ f[0]+offs, f[1]+offs, f[2]+offs ] for f in vol['faces'] ]

        return Volume( vertices = vertices, faces = faces, name=name, color=color )

    @property
    def bbox(self):
        return np.array( [ self.vertices.min(axis=0), self.vertices.max(axis=0) ]  ).T

    @property
    def vertices(self):
        return np.array( self['vertices'] )

    def resize(self, x):
        """ Resize volume by given factor.

        Parameters
        ----------
        x :     int
                Resizing factor
        """

        if not isinstance(self['vertices'], np.ndarray):
            self['vertices'] = np.array(self['vertices'])

        # Get the center
        cn = np.mean( self['vertices'], axis=0 )

        # Get vector from center to each vertex
        vec = self['vertices'] - cn

        # Multiply vector by resize factor
        vec *= x

        # Recalculate vertex positions
        self['vertices'] = vec + cn

    def plot3d(self, **kwargs):
        """Plot neuron using pymaid.plot.plot3d().  

        Parameters
        ----------      
        **kwargs
                Will be passed to plot3d() 
                See help(pymaid.plot3d) for a list of keywords      

        See Also
        --------
        :func:`pymaid.plot3d` 
                    Function called to generate 3d plot   

        Examples
        --------
        >>> vol = pymaid.get_volume('v13.LH_R')        
        >>> vol.plot3d( color = (255,0,0) )
        """

        if 'color' in kwargs:
            self['color'] = kwargs['color']
        
        return plotting.plot3d(self, **kwargs)

    def to_trimesh(self):
        """ Returns trimesh representation of this volume.

        See Also
        --------
        https://github.com/mikedh/trimesh
                trimesh GitHub page.
        """

        try:
            import trimesh
        except:
            raise ImportError('Unable to import trimesh. Please make sure it is installed properly')

        return trimesh.Trimesh( vertices = self['vertices'], faces=self['faces'] )

    def to_2d(self, alpha=0.00017, view='xy',invert_y = False):
        """ Computes the 2d alpha shape (concave hull) this volume. Uses Scipy
        Delaunay and shapely.

        Parameters
        ----------        
        alpha:      float, optional
                    Alpha value to influence the gooeyness of the border. 
                    Smaller numbers don't fall inward as much as larger 
                    numbers. Too large, and you lose everything!
        view :      {'xy','xz','yz'}, optional
                    Determines if frontal, lateral or top view.

        Returns
        -------
        list :      coordinates of 2d circumference
                    e.g. [(x1,y1),(x2,y2),(x3,y3),...]

        """

        def add_edge(edges, edge_points, coords, i, j):
            """ Add a line between the i-th and j-th points,
            if not in the list already.
            """
            if (i, j) in edges or (j, i) in edges:
                # already added
                return
            edges.add( (i, j) )
            edge_points.append(coords[ [i, j] ])

        accepted_views = ['xy','xz','yz']
        

        try:
            from shapely.ops import cascaded_union, polygonize
            import shapely.geometry as geometry
        except:
            raise ImportError('This function needs the <shapely> package.')        

        if view in['xy','yx']:               
            coords = self.vertices[:,[0,1]]
            if invert_y:
                coords[:,1] = coords[:,1] * -1
                
        elif view in ['xz', 'zx']:       
            coords = self.vertices[:,[0,2]]
        elif view in ['yz', 'zy']:        
            coords = self.vertices[:,[1,2]]  
            if invert_y:
                coords[:,0] = coords[:,0] * -1
        else:
            raise ValueError('View {0} unknown. Please use either: {1}'.format(view, accepted_views))      
                           
        tri = scipy.spatial.Delaunay(coords)
        edges = set()
        edge_points = []
        # loop over triangles:
        # ia, ib, ic = indices of corner points of the
        # triangle
        for ia, ib, ic in tri.vertices:
            pa = coords[ia]
            pb = coords[ib]
            pc = coords[ic]
            # Lengths of sides of triangle
            a = math.sqrt((pa[0]-pb[0])**2 + (pa[1]-pb[1])**2)
            b = math.sqrt((pb[0]-pc[0])**2 + (pb[1]-pc[1])**2)
            c = math.sqrt((pc[0]-pa[0])**2 + (pc[1]-pa[1])**2)
            # Semiperimeter of triangle
            s = (a + b + c)/2.0
            # Area of triangle by Heron's formula
            area = math.sqrt(s*(s-a)*(s-b)*(s-c))
            circum_r = a*b*c/(4.0*area)
            # Here's the radius filter.            
            if circum_r < 1.0/alpha:
                add_edge(edges, edge_points, coords, ia, ib)
                add_edge(edges, edge_points, coords, ib, ic)
                add_edge(edges, edge_points, coords, ic, ia)

        m = geometry.MultiLineString(edge_points)
        triangles = list(polygonize(m))
        concave_hull = cascaded_union(triangles)        

        return list(concave_hull.exterior.coords)

