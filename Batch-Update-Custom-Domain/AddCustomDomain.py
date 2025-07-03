import json, pathlib, uuid, sys, requests
from azure.identity import DefaultAzureCredential

# ------------------------------------------------------------------
SUBSCRIPTION_ID = "xxxxxxxxxxxx"
RESOURCE_GROUP  = "xxxxxxxxxxxx"
ACCOUNT_NAME    = "xxxYourBingNamexxxxx"
# ------------------------------------------------------------------

API_VERSION = "2025-05-01-preview"
CONFIG_NAME = f"demo-5domains-{uuid.uuid4().hex[:8]}"

# 1️⃣  read the text file -------------------------------------------------------
domains_file = pathlib.Path("allowed_domains.txt")
if not domains_file.exists():
    sys.exit("allowed_domains.txt not found")

allowed = [{"domain": d.strip()}               # <─ note the key!
           for d in domains_file.read_text().splitlines()
           if d.strip()]

# 2️⃣  build the ARM endpoint ---------------------------------------------------
endpoint = (
    f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
    f"/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.Bing/accounts/{ACCOUNT_NAME}"
    f"/customSearchConfigurations/{CONFIG_NAME}"
    f"?api-version={API_VERSION}"
)

# 3️⃣  get a bearer token -------------------------------------------------------
cred  = DefaultAzureCredential()
token = cred.get_token("https://management.azure.com/.default").token
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# 4️⃣  PUT the configuration ----------------------------------------------------
body = {"properties": {"allowedDomains": allowed, "blockedDomains": []}}
resp = requests.put(endpoint, headers=headers, json=body)
print(resp.status_code, resp.reason)
print(json.dumps(resp.json(), indent=2))

if resp.ok:
    print("\nSUCCESS – configuration name:", CONFIG_NAME)
else:
    print("\nStill failing?  Copy-paste the response and we’ll dig deeper.")
 