import requests
import pandas as pd
from datetime import datetime
from requests.auth import HTTPBasicAuth
import streamlit as st

# ----------------------------------------------------------
# Streamlit page config
# ----------------------------------------------------------
st.set_page_config(
    page_title="Razorpay & Notion – EMI Dashboard",
    layout="wide",
)

st.markdown(
    """
# Razorpay Payments – Token Status Table

This app:

**Tab 1 – Razorpay**
- Fetches Razorpay payments
- Finds the latest token per customer
- Builds a **Name × Date** table showing:
  - `"No Token"` before the token starts  
  - `"Missing"` where there is no payment after token start  
  - Actual Razorpay `status` values on payment dates  

**Tab 2 – Notion**
- Fetches all rows from your Notion EMI database
- Shows them as a table
- Lists any attached PDFs as download/view links
"""
)

# ----------------------------------------------------------
# Razorpay config
# ----------------------------------------------------------
key_id = st.secrets["RAZORPAY_KEY_ID"]
key_secret = st.secrets["RAZORPAY_KEY_SECRET"]

NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
NOTION_DB_ID = st.secrets["NOTION_DB_ID"]

exclude_names_str = st.text_input(
    "Names to exclude (comma-separated, used in Razorpay tab)",
    value="Hardik Jain, Manish, N/A",
    help="These customer names will be removed from the final table in the Razorpay tab.",
)

# Parse excluded names
exclude_names = [n.strip() for n in exclude_names_str.split(",") if n.strip()]

if not key_id or not key_secret:
    st.info("Enter your Razorpay API Key ID and Secret to fetch Razorpay data.")
    st.stop()

BASE_URL = "https://api.razorpay.com/v1"
auth = HTTPBasicAuth(key_id, key_secret)
_customer_cache = {}


def get_customer_name(customer_id):
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


def fetch_all_payments():
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


def build_status_table(df, exclude_names_list):
    """
    From a raw payments DataFrame, build the Name × Date status table using only
    the latest token per user and marking pre-token dates as 'No Token'.
    """
    # We only care about rows with a token and a date
    df = df.dropna(subset=["date", "token_id"])

    # Remove unwanted names
    if exclude_names_list:
        df = df[~df["name"].isin(exclude_names_list)]

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
# Coloring function for Streamlit table (Razorpay tab)
# ----------------------------------------------------------
def color_status(val):
    """Highlight Missing (yellow) and Failed (red)."""
    if isinstance(val, str):
        if val == "Missing":
            return "background-color: yellow; color: black; font-weight: 600;"
        if val.lower() == "failed":
            return "background-color: red; color: white; font-weight: 600;"
    return ""


# ----------------------------------------------------------
# Notion config
# ----------------------------------------------------------

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def fetch_notion_db():
    """
    Fetch all rows from a Notion database and flatten properties into a DataFrame.
    Also returns a list of attached files (with URLs) for download links.
    NOW supports 'formula' properties as well.
    """
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    payload = {"page_size": 100}
    results = []

    while True:
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
        if resp.status_code != 200:
            st.error(f"Error fetching Notion DB: {resp.status_code} – {resp.text}")
            return pd.DataFrame(), []

        data = resp.json()
        results.extend(data.get("results", []))

        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")

    rows = []
    files = []

    for page in results:
        props = page.get("properties", {})
        row = {}

        for prop_name, prop_val in props.items():
            ptype = prop_val.get("type")

            # Title
            if ptype == "title":
                txt = "".join([t.get("plain_text", "") for t in prop_val.get("title", [])])
                row[prop_name] = txt

            # Rich text
            elif ptype == "rich_text":
                txt = "".join([t.get("plain_text", "") for t in prop_val.get("rich_text", [])])
                row[prop_name] = txt

            # Number
            elif ptype == "number":
                row[prop_name] = prop_val.get("number")

            # Select
            elif ptype == "select":
                sel = prop_val.get("select")
                row[prop_name] = sel.get("name") if sel else None

            # Multi-select
            elif ptype == "multi_select":
                ms = prop_val.get("multi_select", [])
                row[prop_name] = ", ".join([o.get("name", "") for o in ms])

            # Date
            elif ptype == "date":
                d = prop_val.get("date")
                row[prop_name] = d.get("start") if d else None

            # Checkbox
            elif ptype == "checkbox":
                row[prop_name] = prop_val.get("checkbox")

            # URL / Email / Phone
            elif ptype == "url":
                row[prop_name] = prop_val.get("url")
            elif ptype == "email":
                row[prop_name] = prop_val.get("email")
            elif ptype == "phone_number":
                row[prop_name] = prop_val.get("phone_number")

            # Files (attachments)
            elif ptype == "files":
                f_list = prop_val.get("files", [])
                file_names = []

                for f in f_list:
                    name = f.get("name")
                    file_obj = f.get("file") or f.get("external")
                    url_f = file_obj.get("url") if file_obj else None

                    if url_f:
                        files.append(
                            {
                                "page_id": page.get("id"),
                                "property": prop_name,
                                "name": name,
                                "url": url_f,
                            }
                        )
                        file_names.append(name)

                if file_names:
                    row[prop_name] = ", ".join(file_names)

            # 🔹 Formula (new part)
            elif ptype == "formula":
                formula_val = prop_val.get("formula") or {}
                ftype = formula_val.get("type")

                if ftype == "string":
                    row[prop_name] = formula_val.get("string")
                elif ftype == "number":
                    row[prop_name] = formula_val.get("number")
                elif ftype == "boolean":
                    row[prop_name] = formula_val.get("boolean")
                elif ftype == "date":
                    d = formula_val.get("date")
                    row[prop_name] = d.get("start") if d else None
                else:
                    # Unknown formula subtype – store None (or raw dict if you prefer)
                    row[prop_name] = None

            # Fallback: ignore other types (relation, rollup, people, etc.)
            else:
                pass

        row["page_id"] = page.get("id")
        rows.append(row)

    df = pd.DataFrame(rows)
    return df, files



# ----------------------------------------------------------
# Tabs layout
# ----------------------------------------------------------
tab1, tab2 = st.tabs(["Razorpay Status Table", "Notion EMI Database"])

# ----------------------------------------------------------
# Tab 1: Razorpay token status table
# ----------------------------------------------------------
with tab1:
    with st.spinner("Fetching payments and building status table from Razorpay..."):
        df_raw = fetch_all_payments()

    if df_raw.empty:
        st.warning("No payments found from Razorpay with the given credentials.")
    else:
        status_table = build_status_table(df_raw, exclude_names)

        st.subheader("Name × Date – Token Status Table")

        if status_table is None or status_table.empty:
            st.warning("No data available after filtering and token processing.")
        else:
            styled_table = status_table.style.applymap(color_status)
            st.dataframe(styled_table, use_container_width=True)

# ----------------------------------------------------------
# Tab 2: Notion database browser (PDFs in separate columns)
# ----------------------------------------------------------
# ----------------------------------------------------------
# Tab 2: Notion database browser (PDFs in separate columns)
# ----------------------------------------------------------
with tab2:
    st.subheader("Notion EMI Database")

    with st.spinner("Fetching data from Notion..."):
        notion_df, notion_files = fetch_notion_db()

    if notion_df.empty:
        st.warning("No rows found in the Notion database.")
    else:
        # --------------------------------------------------
        # Prepare dictionary: page_id → list of PDF files
        # --------------------------------------------------
        pdf_map = {}
        for f in notion_files:
            if ".pdf" in f["url"].lower():
                pid = f["page_id"]
                pdf_map.setdefault(pid, []).append(f)

        # --------------------------------------------------
        # Create separate PDF columns
        # --------------------------------------------------
        loan_agreement_links = []
        kfs_links = []

        for _, row in notion_df.iterrows():
            page_id = row["page_id"]
            files = pdf_map.get(page_id, [])

            loan_link = ""
            kfs_link = ""

            for f in files:
                name = f["name"]
                url = f["url"]

                if "loan" in name.lower():
                    loan_link = f'<a href="{url}" target="_blank">{name}</a>'
                elif "kfs" in name.lower():
                    kfs_link = f'<a href="{url}" target="_blank">{name}</a>'

            loan_agreement_links.append(loan_link)
            kfs_links.append(kfs_link)

        notion_df["Loan Agreement PDF"] = loan_agreement_links
        notion_df["KFS PDF"] = kfs_links

        # --------------------------------------------------
        # Reorder columns: key fields at the top
        # --------------------------------------------------
        preferred_order = [
            "Signed By Name",
            "id",
            "Processing Fees (%)",
            "EMI Amount",
            "Disbursement Amount",
            "Loan Agreement PDF",
            "KFS PDF",
        ]

        # Keep only those that exist, then append the rest
        ordered_cols = [c for c in preferred_order if c in notion_df.columns] + [
            c for c in notion_df.columns if c not in preferred_order
        ]
        notion_df = notion_df[ordered_cols]

        # --------------------------------------------------
        # Show table with HTML links, scrollable horizontally
        # --------------------------------------------------
        table_html = notion_df.to_html(escape=False, index=False)

        scrollable_html = f"""
        <div style="
            max-width: 100%;
            overflow-x: auto;
            white-space: nowrap;
        ">
            {table_html}
        </div>
        """

        st.markdown(scrollable_html, unsafe_allow_html=True)

