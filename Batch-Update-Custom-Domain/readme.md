# AddCustomDomain.py

This script is used to batch add custom allowed/blocked domains to an Azure Bing Search account.

## Overview

- Reads a list of domains from `allowed_domains.txt`
- Builds the Azure ARM API endpoint
- Obtains a Bearer Token for Azure Management API
- Sends a PUT request to configure allowed domains for the specified Bing Search account

## Prerequisites

- Python 3.7+
- Required libraries: `azure-identity`, `requests`
- Azure login with appropriate subscription and resource group permissions
- An `allowed_domains.txt` file in the same directory, with one domain per line

## Install Dependencies

```powershell
pip install azure-identity requests
```

## Usage

1. Edit `allowed_domains.txt` and add one allowed domain per line.
2. Update `SUBSCRIPTION_ID`, `RESOURCE_GROUP`, and `ACCOUNT_NAME` in the script as needed.
3. Run the script:

```powershell
python AddCustomDomain.py
```

## Parameters

- `SUBSCRIPTION_ID`: Your Azure subscription ID
- `RESOURCE_GROUP`: The resource group name
- `ACCOUNT_NAME`: The Bing Search account name

## Output

- On success, prints the HTTP status code, configuration details, and the configuration name.
- On failure, prints the detailed error response for troubleshooting.

## Notes

- You must have sufficient Azure permissions (admin or RBAC).
- The `allowed_domains.txt` file must exist in the same directory as the script.

---

If you encounter errors, please provide the error response