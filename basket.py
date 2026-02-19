import requests
import pandas as pd
from datetime import datetime, date
from requests.auth import HTTPBasicAuth
import streamlit as st
import calendar

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

**Tab 1 – Razorpay**
- Fetches Razorpay payments
- Finds the latest token per customer
- Builds a **Name × Date** table showing:
  - `"No Token"` before token starts
  - `"Missing"` when a payment was expected but not found
  - Razorpay `status` values on payment dates
  - ✅ `"Ended"` after Notion **End Date** has passed (based on **Customer_ID**)
  - ✅ For Weekly/Monthly repayment frequencies, Missing/Failed is evaluated **only on charge dates** (not every day)

- ✅ Adds **Missing Count**, **Failed Count**, and **Due Amount (₹)** at end of each row  
  **Due Amount (₹)** = (Missing + Failed) × EMI Amount (from Notion)

**Tab 2 – Notion**
- Fetches all rows from your EMI database
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

exclude_names = [n.strip() for n in exclude_names_str.split(",") if n.strip()]

if not key_id or not key_secret:
    st.info("Enter your Razorpay API Key ID and Secret to fetch Razorpay data.")
    st.stop()

BASE_URL = "https://api.razorpay.com/v1"
auth = HTTPBasicAuth(key_id, key_secret)
_customer_cache = {}

# ----------------------------------------------------------
# Razorpay helpers
# ----------------------------------------------------------
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
    Supports 'formula' properties as well.
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

            if ptype == "title":
                txt = "".join([t.get("plain_text", "") for t in prop_val.get("title", [])])
                row[prop_name] = txt

            elif ptype == "rich_text":
                txt = "".join([t.get("plain_text", "") for t in prop_val.get("rich_text", [])])
                row[prop_name] = txt

            elif ptype == "number":
                row[prop_name] = prop_val.get("number")

            elif ptype == "select":
                sel = prop_val.get("select")
                row[prop_name] = sel.get("name") if sel else None

            elif ptype == "multi_select":
                ms = prop_val.get("multi_select", [])
                row[prop_name] = ", ".join([o.get("name", "") for o in ms])

            elif ptype == "date":
                d = prop_val.get("date")
                row[prop_name] = d.get("start") if d else None

            elif ptype == "checkbox":
                row[prop_name] = prop_val.get("checkbox")

            elif ptype == "url":
                row[prop_name] = prop_val.get("url")
            elif ptype == "email":
                row[prop_name] = prop_val.get("email")
            elif ptype == "phone_number":
                row[prop_name] = prop_val.get("phone_number")

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
                    row[prop_name] = None
            else:
                pass

        row["page_id"] = page.get("id")
        rows.append(row)

    df = pd.DataFrame(rows)
    return df, files


# ----------------------------------------------------------
# Notion -> mapping helpers
# ----------------------------------------------------------
def build_customer_end_date_map(notion_df):
    """Customer_ID -> latest End Date (max)."""
    if notion_df is None or notion_df.empty:
        return {}
    if "Customer_ID" not in notion_df.columns or "End Date" not in notion_df.columns:
        return {}

    tmp = notion_df[["Customer_ID", "End Date"]].copy()
    tmp["Customer_ID"] = tmp["Customer_ID"].astype(str).str.strip()
    tmp["End Date"] = pd.to_datetime(tmp["End Date"], errors="coerce").dt.normalize()
    tmp = tmp[tmp["Customer_ID"].notna() & (tmp["Customer_ID"] != "")]
    return tmp.groupby("Customer_ID")["End Date"].max().to_dict()


def build_customer_emi_amount_map(notion_df):
    """Customer_ID -> EMI Amount (numeric)."""
    if notion_df is None or notion_df.empty:
        return {}
    if "Customer_ID" not in notion_df.columns or "EMI Amount" not in notion_df.columns:
        return {}

    tmp = notion_df[["Customer_ID", "EMI Amount"]].copy()
    tmp["Customer_ID"] = tmp["Customer_ID"].astype(str).str.strip()
    tmp["EMI Amount"] = pd.to_numeric(tmp["EMI Amount"], errors="coerce")
    tmp = tmp[tmp["Customer_ID"].notna() & (tmp["Customer_ID"] != "")]
    tmp = tmp.dropna(subset=["EMI Amount"])

    if tmp.empty:
        return {}

    return tmp.groupby("Customer_ID")["EMI Amount"].max().to_dict()


def _weekday_str_to_int(s):
    """
    Convert various weekday strings to Python weekday int:
    Monday=0 ... Sunday=6
    Accepts: 'Saturday', 'Sat', 'saturday', etc.
    """
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None

    s = str(s).strip().lower()
    if not s:
        return None

    mapping = {
        "mon": 0, "monday": 0,
        "tue": 1, "tues": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6,
    }
    return mapping.get(s)


def build_customer_schedule_map(notion_df):
    """
    Customer_ID -> schedule dict:
      {
        "frequency": "daily"|"weekly"|"monthly",
        "charge_weekday": 0..6 or None,
        "charge_dom": 1..31 or None
      }

    Uses Notion columns:
      - Customer_ID
      - Repayment Frequency
      - Day_of_Week_to_Charge
      - (optional) Day_of_Month_to_Charge
    """
    if notion_df is None or notion_df.empty:
        return {}

    if "Customer_ID" not in notion_df.columns:
        return {}

    freq_col = "Repayment Frequency"
    dow_col = "Day_of_Week_to_Charge"
    dom_col = "Day_of_Month_to_Charge"  # optional

    tmp_cols = ["Customer_ID"]
    if freq_col in notion_df.columns:
        tmp_cols.append(freq_col)
    if dow_col in notion_df.columns:
        tmp_cols.append(dow_col)
    if dom_col in notion_df.columns:
        tmp_cols.append(dom_col)

    tmp = notion_df[tmp_cols].copy()
    tmp["Customer_ID"] = tmp["Customer_ID"].astype(str).str.strip()
    tmp = tmp[tmp["Customer_ID"].notna() & (tmp["Customer_ID"] != "")]

    # If multiple rows per Customer_ID, take the last non-null-ish values by grouping (practical approach)
    def pick_last_non_null(series):
        s = series.dropna()
        if s.empty:
            return None
        return s.iloc[-1]

    grouped = tmp.groupby("Customer_ID", as_index=False).agg({
        freq_col: pick_last_non_null if freq_col in tmp.columns else pick_last_non_null,
        dow_col: pick_last_non_null if dow_col in tmp.columns else pick_last_non_null,
        dom_col: pick_last_non_null if dom_col in tmp.columns else pick_last_non_null,
    })

    schedule_map = {}
    for _, r in grouped.iterrows():
        cid = r["Customer_ID"]

        freq_raw = r.get(freq_col, None)
        freq = str(freq_raw).strip().lower() if freq_raw is not None and not (isinstance(freq_raw, float) and pd.isna(freq_raw)) else "daily"
        if "week" in freq:
            freq = "weekly"
        elif "month" in freq:
            freq = "monthly"
        else:
            freq = "daily"

        charge_weekday = _weekday_str_to_int(r.get(dow_col, None)) if dow_col in grouped.columns else None

        charge_dom = None
        if dom_col in grouped.columns:
            dom_val = r.get(dom_col, None)
            try:
                charge_dom = int(dom_val) if dom_val is not None and not (isinstance(dom_val, float) and pd.isna(dom_val)) else None
            except Exception:
                charge_dom = None

        schedule_map[cid] = {
            "frequency": freq,
            "charge_weekday": charge_weekday,
            "charge_dom": charge_dom,
        }

    return schedule_map


# ----------------------------------------------------------
# Expected-charge-date logic
# ----------------------------------------------------------
def is_charge_date(ts: pd.Timestamp, schedule: dict) -> bool:
    """
    Decide if a given date is an expected EMI charge date for a customer.
    - daily: every day
    - weekly: only weekday matches charge_weekday (0=Mon ... 6=Sun)
    - monthly: only day-of-month matches charge_dom (if provided)
      If charge_dom is > last day of month, we treat last day as charge day.
    """
    if schedule is None:
        return True  # default daily behavior

    freq = (schedule.get("frequency") or "daily").lower()

    if freq == "daily":
        return True

    if freq == "weekly":
        wd = schedule.get("charge_weekday")
        if wd is None:
            return True  # fallback to daily if not configured
        return ts.weekday() == wd

    if freq == "monthly":
        dom = schedule.get("charge_dom")
        if dom is None:
            return True  # fallback to daily if not configured

        last_dom = calendar.monthrange(ts.year, ts.month)[1]
        effective_dom = min(int(dom), last_dom)
        return ts.day == effective_dom

    return True


# ----------------------------------------------------------
# Build Razorpay status table
# ----------------------------------------------------------
def build_status_table(
    df,
    exclude_names_list,
    customer_to_end_date=None,
    customer_to_emi=None,
    customer_to_schedule=None,
):
    """
    Build Name × Date table for the latest token per user.

    Key behaviors:
    - Pre-token dates => 'No Token'
    - Only EXPECTED charge dates can be Missing (based on repayment schedule)
    - Non-charge dates are blank (""), not Missing/Failed
    - After End Date has passed, future dates > end date => 'Ended'
    - Missing/Failed counts computed only on expected charge dates
    - Due Amount = (Missing + Failed) * EMI Amount
    """
    customer_to_end_date = customer_to_end_date or {}
    customer_to_emi = customer_to_emi or {}
    customer_to_schedule = customer_to_schedule or {}

    df = df.dropna(subset=["date", "token_id"])

    if exclude_names_list:
        df = df[~df["name"].isin(exclude_names_list)]

    if df.empty:
        return None

    df["date"] = df["date"].dt.normalize()

    # token_start_date = earliest payment date per token
    token_start = df.groupby("token_id")["date"].min()
    df["token_start_date"] = df["token_id"].map(token_start)

    # latest token per user (by token_start_date)
    token_summary = (
        df[["name", "customer_id", "token_id", "token_start_date"]]
        .drop_duplicates()
        .sort_values(["name", "token_start_date", "token_id"])
    )
    latest_token_per_user = token_summary.groupby("name").tail(1).reset_index(drop=True)

    # map name -> latest token
    name_to_latest_token = latest_token_per_user.set_index("name")["token_id"].to_dict()
    df["latest_token_id_for_user"] = df["name"].map(name_to_latest_token)

    df_latest = df[df["token_id"] == df["latest_token_id_for_user"]].copy()
    if df_latest.empty:
        return None

    # Date range (still daily columns; we just won't mark missing on non-charge days)
    full_range = pd.date_range(df_latest["date"].min(), df_latest["date"].max(), freq="D")

    # Pivot actual payment statuses
    status_table = df_latest.pivot_table(
        index="name",
        columns="date",
        values="status",
        aggfunc=lambda x: ", ".join(sorted(set(x))),
    )
    status_table = status_table.reindex(columns=full_range)

    # Pre-token => No Token
    PRE_TOKEN_LABEL = "No Token"
    name_to_start = latest_token_per_user.set_index("name")["token_start_date"].to_dict()
    for name in status_table.index:
        start_date = name_to_start.get(name)
        if start_date is None:
            continue
        status_table.loc[name, status_table.columns < start_date] = PRE_TOKEN_LABEL

    # Fill NaN temporarily; we will replace non-charge days with blank afterwards
    status_table = status_table.fillna("Missing")

    # name -> customer_id for latest-token dataset
    name_to_customer = df_latest.groupby("name")["customer_id"].first().to_dict()

    # Apply schedule: non-charge days should be blank ("") instead of Missing
    BLANK_LABEL = ""
    for name in status_table.index:
        cust_id = name_to_customer.get(name)
        sched = customer_to_schedule.get(cust_id, {"frequency": "daily"}) if cust_id else {"frequency": "daily"}

        start_date = name_to_start.get(name)
        if start_date is None:
            continue

        # Only consider dates >= token_start for schedule masking
        for col_date in [c for c in status_table.columns if isinstance(c, pd.Timestamp)]:
            if col_date < start_date:
                continue  # already No Token
            if not is_charge_date(col_date, sched):
                # if there is an actual payment on a non-charge date, keep it; otherwise blank it
                if status_table.loc[name, col_date] == "Missing":
                    status_table.loc[name, col_date] = BLANK_LABEL

    # Ended logic: after End Date has passed, dates after end date => Ended
    ENDED_LABEL = "Ended"
    today = pd.Timestamp(date.today())

    for name in status_table.index:
        cust_id = name_to_customer.get(name)
        if not cust_id:
            continue

        end_date = customer_to_end_date.get(cust_id)
        if end_date is None or pd.isna(end_date):
            continue

        if today > end_date:
            # only mark future dates > end_date as Ended (including blanks/missing)
            for col_date in [c for c in status_table.columns if isinstance(c, pd.Timestamp)]:
                if col_date > end_date:
                    status_table.loc[name, col_date] = ENDED_LABEL

    # Counts (only count literal Missing, and any cell containing "failed")
    def is_failed_cell(x):
        if not isinstance(x, str):
            return False
        return "failed" in x.lower()

    status_table["Missing Count"] = (status_table == "Missing").sum(axis=1)
    status_table["Failed Count"] = status_table.applymap(is_failed_cell).sum(axis=1)

    # EMI + Due Amount
    emi_vals, due_vals = [], []
    for name in status_table.index:
        cust_id = name_to_customer.get(name)
        emi = customer_to_emi.get(cust_id) if cust_id else None

        missing = status_table.loc[name, "Missing Count"]
        failed = status_table.loc[name, "Failed Count"]

        if emi is None or pd.isna(emi):
            emi_vals.append(None)
            due_vals.append(None)
        else:
            emi_vals.append(float(emi))
            due_vals.append(float(emi) * float(missing + failed))

    status_table["EMI Amount (Notion)"] = emi_vals
    status_table["Due Amount (₹)"] = due_vals

    # Pretty date headers only for date columns
    date_cols = [c for c in status_table.columns if isinstance(c, pd.Timestamp)]
    pretty = {d: d.strftime("%d-%b-%Y") for d in date_cols}
    status_table = status_table.rename(columns=pretty)

    return status_table


# ----------------------------------------------------------
# Coloring function for Streamlit table (Razorpay tab)
# ----------------------------------------------------------
def color_status(val):
    """Highlight Missing (yellow), Failed (red), Ended (grey)."""
    if isinstance(val, str):
        if val == "Missing":
            return "background-color: yellow; color: black; font-weight: 600;"
        if val.lower() == "failed":
            return "background-color: red; color: white; font-weight: 600;"
        if val == "Ended":
            return "background-color: #e5e7eb; color: #111827; font-weight: 600;"
    return ""


# ----------------------------------------------------------
# Tabs
# ----------------------------------------------------------
tab1, tab2 = st.tabs(["Razorpay Status Table", "EMI Database"])

# ----------------------------------------------------------
# Tab 1
# ----------------------------------------------------------
with tab1:
    with st.spinner("Fetching payments from Razorpay..."):
        df_raw = fetch_all_payments()

    if df_raw.empty:
        st.warning("No payments found from Razorpay with the given credentials.")
    else:
        with st.spinner("Fetching End Dates + EMI Amounts + Schedules from Notion..."):
            notion_df_for_maps, _ = fetch_notion_db()
            customer_to_end_date = build_customer_end_date_map(notion_df_for_maps)
            customer_to_emi = build_customer_emi_amount_map(notion_df_for_maps)
            customer_to_schedule = build_customer_schedule_map(notion_df_for_maps)

        status_table = build_status_table(
            df_raw,
            exclude_names,
            customer_to_end_date=customer_to_end_date,
            customer_to_emi=customer_to_emi,
            customer_to_schedule=customer_to_schedule,
        )

        st.subheader("Name × Date – Token Status Table")

        if status_table is None or status_table.empty:
            st.warning("No data available after filtering and token processing.")
        else:
            styled_table = status_table.style.applymap(color_status)
            st.dataframe(styled_table, use_container_width=True)

# ----------------------------------------------------------
# Tab 2
# ----------------------------------------------------------
with tab2:
    st.subheader("EMI Database")

    with st.spinner("Fetching data from Notion..."):
        notion_df, notion_files = fetch_notion_db()

    if notion_df.empty:
        st.warning("No rows found in the Notion database.")
    else:
        pdf_map = {}
        for f in notion_files:
            if ".pdf" in f["url"].lower():
                pid = f["page_id"]
                pdf_map.setdefault(pid, []).append(f)

        loan_agreement_links, kfs_links = [], []

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

        preferred_order = [
            "Signed By Name",
            "Customer_ID",
            "Repayment Frequency",
            "Day_of_Week_to_Charge",
            "Day_of_Month_to_Charge",
            "id",
            "EMI Amount",
            "Disbursement Amount",
            "End Date",
            "Processing Fees (%)",
            "Loan Agreement PDF",
            "KFS PDF",
        ]

        ordered_cols = [c for c in preferred_order if c in notion_df.columns] + [
            c for c in notion_df.columns if c not in preferred_order
        ]
        notion_df = notion_df[ordered_cols]

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
