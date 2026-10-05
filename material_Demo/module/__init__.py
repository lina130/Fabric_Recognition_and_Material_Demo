# Module package for CNC and Sensor control
# This file makes the module directory a Python package

from .sensor_module import (
    start_sensor_reading,
    stop_sensor_reading,
    configure_sensor,
    parse_sensor_data,
    is_contact_data
)

from .cnc_module import (
    initialize_cnc,
    execute_command,
    execute_command_and_wait_complete,
    wait_for_idle,
    close_cnc
)

__all__ = [
    # Sensor functions
    'start_sensor_reading',
    'stop_sensor_reading', 
    'configure_sensor',
    'parse_sensor_data',
    'is_contact_data',
    
    # CNC functions
    'initialize_cnc',
    'execute_command',
    'execute_command_and_wait_complete',
    'wait_for_idle',
    'close_cnc'
]