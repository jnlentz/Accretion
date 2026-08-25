"""
Utility functions for precision float formatting, truncation, and step-size alignment.
"""
import math
from typing import Union

def truncate_float(n: float, precision: int) -> float:
    """
    Truncates a float number to a specified number of decimal places without rounding errors.
    Prevents IEEE 754 precision issues when submitting exchange orders.
    """
    if n is None or n == 0:
        return 0.0
    s = f"{n:.12f}"
    dp = s.find('.')
    if dp == -1:
        return float(n)
    if precision <= 0:
        return float(s[:dp])
    out = s[: dp + 1 + precision]
    return float(out)

def round_step_size(quantity: Union[float, int], step_size: Union[float, int]) -> float:
    """
    Rounds quantity down to nearest multiple of step_size.
    Example: round_step_size(0.123456, 0.001) -> 0.123
    """
    if step_size <= 0 or quantity <= 0:
        return 0.0
    precision = get_precision_from_step(step_size)
    steps = math.floor(float(quantity) / float(step_size))
    result = steps * float(step_size)
    return round(result, precision)

def round_tick_size(price: Union[float, int], tick_size: Union[float, int]) -> float:
    """
    Rounds price down to nearest tick_size.
    Example: round_tick_size(45234.5678, 0.01) -> 45234.56
    """
    if tick_size <= 0 or price <= 0:
        return 0.0
    precision = get_precision_from_step(tick_size)
    steps = round(float(price) / float(tick_size))
    result = steps * float(tick_size)
    return round(result, precision)

def get_precision_from_step(step: Union[float, str]) -> int:
    """
    Calculates number of decimal places from step size string or float (e.g. 0.001 -> 3).
    """
    step_str = f"{float(step):.10f}".rstrip('0')
    if '.' in step_str:
        return len(step_str.split('.')[1])
    return 0
