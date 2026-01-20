import google.auth
from google.auth import credentials as google_credentials
import os
from dotenv import load_dotenv

load_dotenv()

def check_creds():
    print("Checking for Google Cloud Credentials...")
    
    # Check environment variable
    env_creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if env_creds:
        print(f"[OK] Found GOOGLE_APPLICATION_CREDENTIALS: {env_creds}")
    else:
        print("[INFO] GOOGLE_APPLICATION_CREDENTIALS environment variable is not set (Using default search paths)")

    try:
        creds, project_id = google.auth.default()
        print(f"[OK] Credentials found successfully!")
        
        if project_id:
            print(f"[OK] Default Project ID: {project_id}")
        else:
            print("[WARN] No default project ID found in credentials (this might be okay if you specify it explicitly)")
            
        if creds.valid:
            print("[OK] Credentials are valid.")
        elif creds.expired:
            print("[WARN] Credentials are expired. Attempting refresh...")
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            if creds.valid:
                 print("[OK] Credentials refreshed and valid.")
            else:
                 print("[FAIL] Could not refresh credentials.")
        else:
            print("[FAIL] Credentials are invalid.")
            
    except google.auth.exceptions.DefaultCredentialsError:
        print("[FAIL] error: Could not find any credentials.")
        print("Run the following command to log in:")
        print('cmd /c "gcloud auth application-default login"')
    except Exception as e:
        print(f"[FAIL] An error occurred: {e}")

if __name__ == "__main__":
    check_creds()
