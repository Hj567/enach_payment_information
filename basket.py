import requests
import pandas as pd
from datetime import datetime
from requests.auth import HTTPBasicAuth
import streamlit as st

# ----------------------------------------------------------
# Streamlit page config
# ----------------------------------------------------------
st.set_page_config(
    page_title="Razorpay Payments – Token Status Table",
    layout="wide",
)

st.markdown(
    """
# Razorpay Payments – Token Status Table

This app fetches Razorpay payments, finds the latest token per customer, and builds a **Name × Date** table that shows:

- `"No Token"` before the token starts  
- `"Missing"` where there is no payment after token start  
- Actual Razorpay `status` values on payment dates  
"""
)

# ----------------------------------------------------------
# Inputs (no button – everything is automatic)
# ----------------------------------------------------------

key_id = "rzp_live_RmhBQY0fhwdaM5"
key_secret = "1xlKDlEeoLolPGAekPuxcEJZ"


exclude_names_str = st.text_input(
    "Names to exclude (comma-separated)",
    value="Hardik Jain, Manish, N/A",
    help="These customer names will be removed from the final table.",
)

# Parse excluded names
exclude_names = [n.strip() for n in exclude_names_str.split(",") if n.strip()]

if not key_id or not key_secret:
    st.info("Enter your Razorpay API Key ID and Secret to fetch data.")
    st.stop()

# ----------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------
BASE_URL = "https://api.razorpay.com/v1"
auth = HTTPBasicAuth(key_id, key_secret)
_customer_cache: dict[str, str] = {}


def get_customer_name(customer_id: str) -> str:
    """Fetch customer name from Razorpay (with a simple in-memory cache)."""
    if not customer_id:
        return "N/A"

    if customer_id in _customer_cache:
        return _customer_cache[customer_id]

    try:
        resp = requests.get(f"{BASE_URL}/customers/{customer_id}", auth=auth)
        if resp.status_code == 200:
            name = resp.json().get("name", "N/A")
            _customer_cache[customer_id] = name
            return name
    except Exception:
        pass

    _customer_cache[customer_id] = "N/A"
    return "N/A"


def fetch_all_payments() -> pd.DataFrame:
    """
    Fetch ALL Razorpay payments (paginated) and return as a DataFrame
    with columns: name, customer_id, token_id, date, status.
    """
    rows = []
    skip = 0
    count = 100  # Razorpay max per page

    while True:
        url = f"{BASE_URL}/payments?count={count}&skip={skip}"
        resp = requests.get(url, auth=auth)

        if resp.status_code != 200:
            st.error(f"Error fetching payments: {resp.status_code} – {resp.text}")
            break

        data = resp.json()
        items = data.get("items", [])
        if not items:
            break

        for entry in items:
            ts = entry.get("created_at")
            date_dt = datetime.fromtimestamp(ts) if ts else None

            rows.append(
                {
                    "name": get_customer_name(entry.get("customer_id")),
                    "customer_id": entry.get("customer_id"),
                    "token_id": entry.get("token_id"),
                    "date": date_dt,
                    "status": entry.get("status"),
                }
            )

        skip += count

    return pd.DataFrame(rows)


def build_status_table(df: pd.DataFrame, exclude_names: list[str]) -> pd.DataFrame | None:
    """
    From a raw payments DataFrame, build the Name × Date status table using only
    the latest token per user and marking pre-token dates as 'No Token'.
    """
    # We only care about rows with a token and a date
    df = df.dropna(subset=["date", "token_id"])

    # Remove unwanted names
    if exclude_names:
        df = df[~df["name"].isin(exclude_names)]

    if df.empty:
        return None

    # Date-only
    df["date"] = df["date"].dt.normalize()

    # 1) token_start_date = earliest payment date for each token
    token_start = df.groupby("token_id")["date"].min()
    df["token_start_date"] = df["token_id"].map(token_start)

    # 2) For each user, keep only their latest token (by token_start_date)
    token_summary = (
        df[["name", "customer_id", "token_id", "token_start_date"]]
        .drop_duplicates()
        .sort_values(["name", "token_start_date", "token_id"])
    )

    latest_token_per_user = (
        token_summary.groupby("name")
        .tail(1)  # last = latest token for that user
        .reset_index(drop=True)
    )

    # Map name -> latest token_id
    name_to_latest_token = latest_token_per_user.set_index("name")["token_id"].to_dict()
    df["latest_token_id_for_user"] = df["name"].map(name_to_latest_token)

    # Keep only payments done with the latest token
    df_latest = df[df["token_id"] == df["latest_token_id_for_user"]].copy()
    if df_latest.empty:
        return None

    # 3) Build continuous date range and pivot
    full_range = pd.date_range(df_latest["date"].min(), df_latest["date"].max(), freq="D")

    status_table = df_latest.pivot_table(
        index="name",
        columns="date",
        values="status",
        aggfunc=lambda x: ", ".join(sorted(set(x))),
    )

    # Ensure all dates included as columns
    status_table = status_table.reindex(columns=full_range)

    # 4) Mark dates before each user's token_start_date as "No Token"
    PRE_TOKEN_LABEL = "No Token"
    name_to_start = latest_token_per_user.set_index("name")["token_start_date"].to_dict()

    for name in status_table.index:
        start_date = name_to_start.get(name)
        if start_date is None:
            continue
        mask_pre = status_table.columns < start_date
        status_table.loc[name, mask_pre] = PRE_TOKEN_LABEL

    # 5) Remaining NaNs (after token start but no payment) → "Missing"
    status_table = status_table.fillna("Missing")

    # Pretty date headers
    status_table.columns = [d.strftime("%d-%b-%Y") for d in status_table.columns]

    return status_table


# ----------------------------------------------------------
# Build table automatically (no button)
# ----------------------------------------------------------
with st.spinner("Fetching payments and building status table..."):
    df_raw = fetch_all_payments()

if df_raw.empty:
    st.warning("No payments found from Razorpay with the given credentials.")
    st.stop()

status_table = build_status_table(df_raw, exclude_names)

st.subheader("Name × Date – Token Status Table")

if status_table is None or status_table.empty:
    st.warning("No data available after filtering and token processing.")
else:
    st.dataframe(status_table, use_container_width=True)
