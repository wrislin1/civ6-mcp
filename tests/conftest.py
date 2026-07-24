import os

# Tests must never call Calculator.
os.environ["CIV6_REGISTRY_OFFLINE"] = "1"
