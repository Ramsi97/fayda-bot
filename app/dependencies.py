# app/dependencies.py
from app.instances import processor, billing

def get_processing_service():
    # Return the singleton processor shared across all requests
    return processor

def get_billing_service():
    # Return the singleton billing service shared across all requests
    return billing