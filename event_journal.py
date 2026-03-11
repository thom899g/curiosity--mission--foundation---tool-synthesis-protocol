"""
Event Journal First Architecture Implementation
Core component for immutable event streaming and state reconstruction.
Uses Firebase Firestore as the primary append-only event store.
"""
import json
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union
from google.api_core import retry as google_retry
from google.cloud import firestore
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter
import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore
import structlog
from threading import Lock

logger = structlog.get_logger(__name__)

class EventJournalError(Exception):
    """Base exception for Event Journal errors"""
    pass

class EventValidationError(EventJournalError):
    """Raised when event data fails validation"""
    pass

class EventJournal:
    """
    Append-only event log with real-time subscribers and causal ordering.
    Implements Event Sourcing pattern with Firebase Firestore backend.
    """
    
    # Event types for the system
    EVENT_TYPES = {
        "TOOL_DEFINED": "tool_defined",
        "EXECUTION_STARTED": "execution_started",
        "OUTPUT_CHUNK": "output_chunk",
        "EXECUTION_COMPLETED": "execution_completed",
        "EXECUTION_FAILED": "execution_failed",
        "SYSTEM_ALERT": "system_alert",
        "HEARTBEAT": "heartbeat",
        "DEPENDENCY_RESOLVED": "dependency_resolved",
        "RESOURCE_ALLOCATED": "resource_allocated"
    }
    
    def __init__(self, credentials_path: Optional[str] = None, 
                 project_id: Optional[str] = None):
        """
        Initialize Event Journal with Firebase connection.
        
        Args:
            credentials_path: Path to encrypted credentials file
            project_id: Firebase project ID (optional, auto-detected)
        
        Raises:
            EventJournalError: If Firebase initialization fails
        """
        self.log = logger.bind(component="event_journal")
        self._initialized = False
        self._listeners = {}
        self._listener_lock = Lock()
        
        try:
            self._initialize_firebase(credentials_path, project_id)
            self._initialized = True
            self.log.info("event_journal_initialized", project_id=project_id)
        except Exception as e:
            self.log.error("event_journal_initialization_failed", error=str(e))
            raise EventJournalError(f"Failed to initialize Event Journal: {e}")
    
    def _initialize_firebase(self, credentials_path: Optional[str], 
                           project_id: Optional[str]):
        """Initialize Firebase Admin SDK with secure credential handling"""
        try:
            # Check if already initialized
            if firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
                self.log.debug("firebase_already_initialized")
                app = firebase_admin.get_app()
            else:
                if credentials_path:
                    # Decrypt credentials if they're encrypted
                    if credentials_path.endswith('.enc'):
                        creds_dict = self._decrypt_credentials(credentials_path)
                        # Write to temp file for Certificate method
                        import tempfile
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',