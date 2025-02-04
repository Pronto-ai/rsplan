"""
The Reeds-Shepp algorithm attempts to find the shortest path in the XY plane with a
start and end (X, Y) position and yaw angle. The path is for a non-holonomic robot which
must have a turn radius. The robot needs to be able to travel in forward
and reverse and the path is allowed to have cusps (transitions between forward and
reverse). Based on these constraints, this planner will return the shortest path between
the two poses regardless of any external factors such as obstacles or barriers.

Paper providing more information on the algorithm:
Reeds, J., & Shepp, L. (1990). Optimal paths for a car that goes both forwards
and backwards. https://msp.org/pjm/1990/145-2/pjm-v145-n2-p06-s.pdf
Curve formulas can be found on page 390-391.

Inspiration for this Python implementation of the Reeds-Shepp algorithm:
https://github.com/boyali/reeds_and_shepp_curves/tree/master

Our implementation adds the option for a runway (straightaway segment at the end of the
path) to improve precision in reaching the given end pose. We also prioritize a path
with fewer segments over a path with a shorter overall length as long as the two paths
have a total length within 2 meters of each others.
"""

from typing import List, Literal, Tuple

import numpy as np

from rsplan import curves, helpers, primitives
from rsplan.params import *


def path(
    start_pose: Tuple[float, float, float],
    end_pose: Tuple[float, float, float],
    turn_radius: float,
    runway_length: float,
    step_size: float,
    length_tolerance: float = 2.0,
) -> primitives.Path:
    """Generates a Reeds-Shepp path given start and end points (represented as
    [x, y, yaw]), turn radius, and step size. The step size is the distance between each
    point in the list of points (e.g. 0.05m).
    
    If the path has no runway, the runway length must be 0.0.
    
    If the path has a runway, calculates the runway start pose, creates a Reeds-Shepp
    path from the given start pose to the runway start pose, and adds a straight Segment
    to the end of the list of Segments in the Path. The runway Segment is a straightaway
    intended to improve the final position accuracy of navigation. The runway can either
    be driven in forward or reverse depending on the sign of the runway length specified
    and can have a variable length.
    """
    if runway_length != 0:  # Path has a runway
        runway_direction: Literal[-1, 1] = -1 if runway_length < 0.0 else 1
        abs_runway_length = abs(runway_length)

        runway_start_pose = _calc_runway_start_pose(
            end_pose, runway_direction, abs_runway_length
        )

        # Find all Reeds-Shepp paths and choose optimal one
        all_paths = _solve_path(start_pose, runway_start_pose, turn_radius, step_size)
        path_rs, cost = _get_optimal_path(all_paths, length_tolerance)
        if path_rs is None:
            return None, None

        # Add runway Segment to Path list of Segments
        runway_segment = _calc_runway_segment(
            runway_start_pose, end_pose, runway_direction, turn_radius
        )
        segments = path_rs.segments + [runway_segment]
    else:
        # Find all Reeds-Shepp paths and choose optimal one
        all_paths = _solve_path(start_pose, end_pose, turn_radius, step_size)
        path_rs, cost = _get_optimal_path(all_paths, length_tolerance)
        if path_rs is None:
            return None, None
        segments = path_rs.segments

    return primitives.Path(
        start_pose=start_pose,
        end_pose=end_pose,
        segments=segments,
        turn_radius=turn_radius,
        step_size=step_size,
    ), cost

def get_valid_paths(
    start_pose: Tuple[float, float, float],
    end_pose: Tuple[float, float, float],
    turn_radius: float,
    step_size: float,
    max_num_cusp_pts: int,
) -> List[primitives.Path]:
    """Generates a list of Reeds-Shepp paths filtered by various criteria given start and end points 
    (represented as [x, y, yaw]), turn radius, and step size. The step size is the 
    distance between each point in the list of points (e.g. 0.05m).
    """
    # Find all Reeds-Shepp paths and choose optimal one
    all_paths = _solve_path(start_pose, end_pose, turn_radius, step_size)
    paths = _get_sorted_valid_paths(all_paths, max_num_cusp_pts)
    if len(paths) == 0:
        return None
    ret_paths = [(primitives.Path(
        start_pose=start_pose,
        end_pose=end_pose,
        segments=path[0].segments,
        turn_radius=turn_radius,
        step_size=step_size,
    ), path[1]) for path in paths]
    return ret_paths

########################################################################################
# PATH PLANNING HELPER FUNCTIONS #######################################################
########################################################################################


def _solve_path(
    start: Tuple[float, float, float],
    end: Tuple[float, float, float],
    turn_rad: float,
    step_size: float,
) -> List[primitives.Path]:
    """Calls all 6 curve functions and returns a list of all valid Reeds-Shepp paths."""
    # If start is not origin, get end w.r.t. start instead of w.r.t. origin
    x, y, phi = helpers.change_base(start, end)

    # Create list of all Reeds-Shepp paths
    paths: List[primitives.Path] = []
    paths.extend(curves.csc(start, end, step_size, x, y, phi, turn_rad))
    paths.extend(curves.ccc(start, end, step_size, x, y, phi, turn_rad))
    paths.extend(curves.cccc(start, end, step_size, x, y, phi, turn_rad))
    paths.extend(curves.ccsc(start, end, step_size, x, y, phi, turn_rad))
    paths.extend(curves.cscc(start, end, step_size, x, y, phi, turn_rad))
    paths.extend(curves.ccscc(start, end, step_size, x, y, phi, turn_rad))

    return paths


def _get_path_cost(path: primitives.Path):
    """Compute path cost"""
    return LENGTH_COST * path.total_length + CUSP_COST * path.number_of_cusp_points + sum([REV_COST * abs(seg.length) for seg in path.segments if seg.direction == -1])

def _get_optimal_path(
    paths: List[primitives.Path], length_tolerance: float = 2.0
) -> primitives.Path:
    """Choose optimal Reeds-Shepp path from list of valid paths. If two paths have
    comparable total length (difference is within length tolerance in meters), we will
    choose the path that has less segments.
    """
    paths = [path for path in paths if path.number_of_cusp_points < 3 and path.segments[0].direction == 1]
    if len(paths) == 0:
        return None, None
    paths.sort(key=_get_path_cost, reverse=False)

    return paths[0], _get_path_cost(paths[0])
    

def _get_sorted_valid_paths(
    paths: List[primitives.Path], max_num_cusp_pts: int
) -> List[Tuple[primitives.Path, float]]:
    """Return list of valid trails sorted by cost from lowest to highest"""
    assert max_num_cusp_pts < 3, "max_num_cusp_pts must be less than 3"
    """ explanation for first filter: 
        * if max_num_cusp_pts is 1, it's for a y-turn entry because we can approach the backup maneuver in drive or reverse
        * if max_num_cusp_pts is 2, it's for all other modalities, but since those require approaching only in drive,
            we can't have only one cusp point, so it's either 0 or 2
    """
    paths = [path for path in paths if ((path.number_of_cusp_points == 0 or path.number_of_cusp_points == max_num_cusp_pts)
                                        and path.segments[0].direction == 1
                                        and sum([abs(seg.length) for seg in path.segments if seg.direction == -1]) < MAX_REV_FRACTION * path.total_length)]
    paths.sort(key=_get_path_cost, reverse=False)

    return [(path, _get_path_cost(path)) for path in paths]


def _calc_runway_start_pose(
    end_pose: Tuple[float, float, float],
    driving_direction: Literal[1, -1],
    runway_length: float,
) -> Tuple[float, float, float]:
    """The start of the runway. Driving direction indicates whether the robot is
    travelling in forward or reverse along the runway.
    """
    end_x, end_y, yaw = end_pose  # Yaw is in radians
    x = end_x - (driving_direction * abs(runway_length) * np.cos(yaw))
    y = end_y - (driving_direction * abs(runway_length) * np.sin(yaw))

    return x, y, yaw


def _calc_runway_segment(
    start_pose: Tuple[float, float, float],
    end_pose: Tuple[float, float, float],
    direction: Literal[1, -1],
    turn_radius: float,
) -> primitives.Segment:
    """Creates a straight Segment representing a path's runway."""
    path_length = round(helpers.euclidean_distance(start_pose, end_pose), 3)

    return primitives.Segment(
        type="straight",
        direction=direction,
        length=path_length,
        turn_radius=turn_radius,
    )
