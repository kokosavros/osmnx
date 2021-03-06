###################################################################################################
# Module: projection.py
# Description: Project spatial geometries and street networks to/from UTM
# License: MIT, see full license in LICENSE.txt
# Web: https://github.com/gboeing/osmnx
###################################################################################################

import time
import math
import numpy as np
import geopandas as gpd
import networkx as nx
from shapely.geometry import Point

from .utils import log, make_str


def project_geometry(geometry, crs, to_latlong=False):
    """
    Project a shapely Polygon or MultiPolygon from lat-long to UTM, or vice-versa
    
    Parameters
    ----------
    geometry : shapely Polygon or MultiPolygon, the geometry to project
    crs : the starting coordinate reference system of the passed-in geometry
    to_latlong : if True, project from crs to lat-long, if False, project from crs to local UTM zone
    
    Returns
    -------
    geometry_proj, crs : tuple (projected shapely geometry, crs of the projected geometry)
    """
    gdf = gpd.GeoDataFrame()
    gdf.crs = crs
    gdf.name = 'geometry to project'
    gdf['geometry'] = None
    gdf.loc[0, 'geometry'] = geometry
    gdf_proj = project_gdf(gdf, to_latlong=to_latlong)
    geometry_proj = gdf_proj['geometry'].iloc[0]
    return geometry_proj, gdf_proj.crs


def project_gdf(gdf, to_latlong=False):
    """
    Project a GeoDataFrame to the UTM zone appropriate for its geometries' centroid. The simple calculation
    in this function works well for most latitudes, but won't work for some far northern locations like
    Svalbard and parts of far northern Norway.
    
    Parameters
    ----------
    gdf : GeoDataFrame, the gdf to be projected to UTM
    to_latlong : bool, if True, projects to latlong instead of to UTM
    
    Returns
    -------
    gdf : GeoDataFrame
    """
    assert len(gdf) > 0, 'You cannot project an empty GeoDataFrame.'
    start_time = time.time()
    
    if to_latlong:
        # if to_latlong is True, project the gdf to latlong
        latlong_crs = {'init':'epsg:4326'}
        projected_gdf = gdf.to_crs(latlong_crs)
        if not hasattr(gdf, 'name'):
            gdf.name = 'unnamed'
        log('Projected the GeoDataFrame "{}" to EPSG 4326 in {:,.2f} seconds'.format(gdf.name, time.time()-start_time))
    else:
        # else, project the gdf to UTM
        # if GeoDataFrame is already in UTM, just return it
        if (not gdf.crs is None) and ('proj' in gdf.crs) and (gdf.crs['proj'] == 'utm'):
            return gdf
        
        # calculate the centroid of the union of all the geometries in the GeoDataFrame
        avg_longitude = gdf['geometry'].unary_union.centroid.x
        
        # calculate the UTM zone from this avg longitude and define the UTM CRS to project
        utm_zone = int(math.floor((avg_longitude + 180) / 6.) + 1)
        utm_crs = {'datum': 'NAD83',
                   'ellps': 'GRS80',
                   'proj' : 'utm',
                   'zone' : utm_zone,
                   'units': 'm'}
        
        # project the GeoDataFrame to the UTM CRS
        projected_gdf = gdf.to_crs(utm_crs)
        if not hasattr(gdf, 'name'):
            gdf.name = 'unnamed'
        log('Projected the GeoDataFrame "{}" to UTM-{} in {:,.2f} seconds'.format(gdf.name, utm_zone, time.time()-start_time))
    
    projected_gdf.name = gdf.name
    return projected_gdf

    
def project_graph(G):
    """
    Project a graph from lat-long to the UTM zone appropriate for its geographic location.
    
    Parameters
    ----------
    G : graph, the networkx graph to be projected to UTM
    
    Returns
    -------
    G_proj : graph
    """
    
    G_proj = G.copy()
    start_time = time.time()
    
    # create a GeoDataFrame of the nodes, name it, convert osmid to str
    nodes = {node:data for node, data in G_proj.nodes(data=True)}
    gdf_nodes = gpd.GeoDataFrame(nodes).T
    gdf_nodes.crs = G_proj.graph['crs']
    gdf_nodes.name = '{}_nodes'.format(G_proj.name)
    gdf_nodes['osmid'] = gdf_nodes['osmid'].astype(np.int64).map(make_str)
    
    # create new lat/lon columns just to save that data for later, and create a geometry column from x/y
    gdf_nodes['lon'] = gdf_nodes['x']
    gdf_nodes['lat'] = gdf_nodes['y']
    gdf_nodes['geometry'] = gdf_nodes.apply(lambda row: Point(row['x'], row['y']), axis=1)
    log('Created a GeoDataFrame from graph in {:,.2f} seconds'.format(time.time()-start_time))
    
    # project the nodes GeoDataFrame to UTM
    gdf_nodes_utm = project_gdf(gdf_nodes)
    
    # extract data for all edges that have geometry attribute
    edges_with_geom = []
    for u, v, key, data in G_proj.edges(keys=True, data=True):
        if 'geometry' in data:
            edges_with_geom.append({'u':u, 'v':v, 'key':key, 'geometry':data['geometry']})
    
    # create an edges GeoDataFrame and project to UTM, if there were any edges with a geometry attribute
    # geom attr only exists if graph has been simplified, otherwise you don't have to project anything for the edges because the nodes still contain all spatial data
    if len(edges_with_geom) > 0:
        gdf_edges = gpd.GeoDataFrame(edges_with_geom)
        gdf_edges.crs = G_proj.graph['crs']
        gdf_edges.name = '{}_edges'.format(G_proj.name)
        gdf_edges_utm = project_gdf(gdf_edges)
    
    # extract projected x and y values from the nodes' geometry column
    start_time = time.time()
    gdf_nodes_utm['x'] = gdf_nodes_utm['geometry'].map(lambda point: point.x)
    gdf_nodes_utm['y'] = gdf_nodes_utm['geometry'].map(lambda point: point.y)
    gdf_nodes_utm = gdf_nodes_utm.drop('geometry', axis=1)
    log('Extracted projected node geometries from GeoDataFrame in {:,.2f} seconds'.format(time.time()-start_time))
    
    # clear the graph to make it a blank slate for the projected data
    start_time = time.time()
    edges = list(G_proj.edges(keys=True, data=True))
    graph_name = G_proj.graph['name']
    G_proj.clear()
    
    # add the projected nodes and all their attributes to the graph
    G_proj.add_nodes_from(gdf_nodes_utm.index)
    attributes = gdf_nodes_utm.to_dict()
    for name in gdf_nodes_utm.columns:
        nx.set_node_attributes(G_proj, name, attributes[name])

    # add the edges and all their attributes (including reconstructed geometry, when it exists) to the graph
    for u, v, key, attributes in edges:
        if 'geometry' in attributes:
            row = gdf_edges_utm[(gdf_edges_utm['u']==u) & (gdf_edges_utm['v']==v) & (gdf_edges_utm['key']==key)]
            attributes['geometry'] = row['geometry'].iloc[0]
        G_proj.add_edge(u, v, key=key, **attributes)
    
    # set the graph's CRS attribute to the new, projected CRS and return the projected graph
    G_proj.graph['crs'] = gdf_nodes_utm.crs
    G_proj.graph['name'] = '{}_UTM'.format(graph_name)
    if 'streets_per_node' in G.graph:
        G_proj.graph['streets_per_node'] = G.graph['streets_per_node']
    log('Rebuilt projected graph in {:,.2f} seconds'.format(time.time()-start_time))
    return G_proj

    