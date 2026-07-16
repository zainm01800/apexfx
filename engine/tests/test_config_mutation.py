import sys
import pytest
from apex_quant.config import get_config

def test_config_not_mutated_on_import():
    # Save the original min_position value from config
    original_min_position = get_config().risk.min_position
    
    # Import the live scanner module (which used to mutate config at module level)
    import scripts.run_live_paper_trading as scanner
    
    # Check that the process-wide cached config has not changed
    current_min_position = get_config().risk.min_position
    
    assert current_min_position == original_min_position, (
        f"Importing scripts.run_live_paper_trading mutated the process-wide config! "
        f"Expected {original_min_position}, got {current_min_position}"
    )
