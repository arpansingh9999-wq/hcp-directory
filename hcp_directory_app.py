import os
import re
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import streamlit as st


NPI_API_URL = "https://npiregistry.cms.hhs.gov/api/"
ESTIMATE_SERVICE_URL = "https://api.openai.com/v1/responses"
DEFAULT_ESTIMATE_MODEL = "gpt-4.1-mini"
AFFILIATION_FILE_NAMES = (
    "cms.cms_hospital_master_ccn.xlsx",
    "affiliations.xlsx",
    "affiliations.xls",
    "affiliations.csv",
    "affiliation_data.xlsx",
    "affiliation_data.xls",
    "affiliation_data.csv",
    "hospital_affiliations.xlsx",
    "hospital_affiliations.xls",
    "hospital_affiliations.csv",
)
AFFILIATION_FILE_STEMS = ("affiliations", "affiliation_data", "hospital_affiliations")
AFFILIATION_DISPLAY_COLUMNS = {
    "affiliated_hospital": "Affiliated Hospital",
    "hospital_address": "Hospital Address",
    "hospital_city": "Hospital City",
    "hospital_state": "Hospital State",
    "hospital_zip_code": "Hospital Zip Code",
    "hospital_phone_number": "Hospital Phone Number",
    "hospital_type": "Hospital Type",
}


st.set_page_config(
    page_title="HCP Directory",
    page_icon="H",
    layout="wide",
)


def clean_text(value: Any, fallback: str = "Not Available") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def split_name(search_text: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"\s+", search_text.strip()) if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def clean_npi(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def build_search_label(hcp_name: str, npi_number: str, state: str, specialty: str) -> str:
    filters = []
    if hcp_name:
        filters.append(hcp_name)
    if npi_number:
        filters.append(f"NPI {npi_number}")
    if state:
        filters.append(state.upper())
    if specialty:
        filters.append(specialty)
    return ", ".join(filters) if filters else "Your Search"


def format_address(address: dict[str, Any] | None) -> str:
    if not address:
        return "Not Available"

    street = " ".join(
        clean_text(address.get(key), "")
        for key in ("address_1", "address_2")
        if clean_text(address.get(key), "")
    )
    city_state_zip = ", ".join(
        clean_text(address.get(key), "")
        for key in ("city", "state", "postal_code")
        if clean_text(address.get(key), "")
    )
    country = clean_text(address.get("country_name"), "")
    phone = clean_text(address.get("telephone_number"), "")

    pieces = [piece for piece in (street, city_state_zip, country) if piece]
    if phone:
        pieces.append(f"Phone: {phone}")
    return "\n\n".join(pieces) if pieces else "Not Available"


def get_location(addresses: list[dict[str, Any]], purpose: str) -> dict[str, Any] | None:
    return next(
        (address for address in addresses if address.get("address_purpose") == purpose),
        None,
    )


def render_footer() -> None:
    st.divider()
    st.caption("Developed By Arpan Singh | Qral Group")


def get_secret_value(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value).strip() or os.getenv(name, "").strip()


def normalize_column_name(column_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", column_name.lower()).strip("_")


def find_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized_columns = {normalize_column_name(column): column for column in columns}
    for candidate in candidates:
        if candidate in normalized_columns:
            return normalized_columns[candidate]
    return None


def find_affiliation_file() -> Path | None:
    app_dir = Path(__file__).resolve().parent
    search_dirs = (
        app_dir,
        app_dir / "Excel",
        app_dir.parent / "Excel",
        app_dir.parent.parent / "Excel",
    )
    for search_dir in search_dirs:
        for filename in AFFILIATION_FILE_NAMES:
            candidate = search_dir / filename
            if candidate.exists():
                return candidate
    return None


def excel_column_index(cell_reference: str) -> int:
    letters = "".join(character for character in cell_reference if character.isalpha())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter.upper()) - ord("A") + 1
    return index - 1


def read_xlsx_with_stdlib(file_path: Path) -> pd.DataFrame:
    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(file_path) as workbook:
        shared_strings = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            for shared_item in shared_root.findall("a:si", namespace):
                shared_strings.append(
                    "".join(
                        text_node.text or ""
                        for text_node in shared_item.findall(".//a:t", namespace)
                    )
                )

        sheet_root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet_root.findall(".//a:sheetData/a:row", namespace):
            values = []
            for cell in row.findall("a:c", namespace):
                cell_index = excel_column_index(cell.attrib.get("r", "A1"))
                while len(values) <= cell_index:
                    values.append("")

                value_node = cell.find("a:v", namespace)
                inline_node = cell.find("a:is/a:t", namespace)
                if cell.attrib.get("t") == "s" and value_node is not None:
                    values[cell_index] = shared_strings[int(value_node.text)]
                elif inline_node is not None:
                    values[cell_index] = inline_node.text or ""
                elif value_node is not None:
                    values[cell_index] = value_node.text or ""
            rows.append(values)

    if not rows:
        return pd.DataFrame()

    headers = [str(header).strip() for header in rows[0]]
    data_rows = []
    for row in rows[1:]:
        padded_row = row + [""] * (len(headers) - len(row))
        data_rows.append(padded_row[: len(headers)])
    return pd.DataFrame(data_rows, columns=headers)


def read_affiliation_file(file_path: Path) -> pd.DataFrame:
    if file_path.suffix.lower() == ".csv":
        return pd.read_csv(file_path, dtype=str)
    try:
        return pd.read_excel(file_path, dtype=str)
    except ImportError:
        if file_path.suffix.lower() == ".xlsx":
            return read_xlsx_with_stdlib(file_path)
        raise


@st.cache_data(show_spinner=False, ttl=3600)
def load_affiliation_data() -> tuple[pd.DataFrame, str, list[str], str]:
    file_path = find_affiliation_file()
    if not file_path:
        return pd.DataFrame(), "", [], ""

    df = read_affiliation_file(file_path)
    df = df.fillna("")
    df.columns = [str(column).strip() for column in df.columns]

    npi_column = find_column(
        list(df.columns),
        (
            "npi",
            "npi_number",
            "provider_npi",
            "hcp_npi",
            "national_provider_identifier",
        ),
    )
    if not npi_column:
        raise ValueError("Could not find an NPI column in the backend affiliation file.")

    display_columns = []
    normalized_columns = {normalize_column_name(column): column for column in df.columns}
    for normalized_name in AFFILIATION_DISPLAY_COLUMNS:
        column = normalized_columns.get(normalized_name)
        if column:
            display_columns.append(column)

    df["_npi_clean"] = df[npi_column].astype(str).map(clean_npi)
    return df, npi_column, display_columns, file_path.name


def render_affiliations(npi: str) -> None:
    st.markdown("**Affiliations**")

    try:
        affiliation_df, _, display_columns, source_file = load_affiliation_data()
    except ValueError as exc:
        st.warning(str(exc))
        return

    if affiliation_df.empty:
        st.info(
            "No backend affiliation file found. Add cms.cms_hospital_master_ccn.xlsx "
            "to the app folder or an Excel folder."
        )
        return

    matches = affiliation_df[affiliation_df["_npi_clean"] == clean_npi(npi)].copy()
    if matches.empty:
        st.info("No affiliations found for this NPI in the backend affiliation file.")
        return

    if not display_columns:
        st.warning(
            "Affiliation file was found, but expected hospital columns were not found."
        )
        return

    display_df = matches[display_columns].rename(
        columns={
            column: AFFILIATION_DISPLAY_COLUMNS.get(normalize_column_name(column), column)
            for column in display_columns
        }
    )
    st.caption(f"Source: {source_file}")
    st.dataframe(display_df, width="stretch", hide_index=True)


def extract_response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return clean_text(data["output_text"])

    text_parts = []
    for output_item in data.get("output", []):
        for content_item in output_item.get("content", []):
            if content_item.get("type") in {"output_text", "text"}:
                text_parts.append(clean_text(content_item.get("text"), ""))

    response_text = "\n\n".join(part for part in text_parts if part)
    return response_text or "No Estimate Was Returned."


def build_patient_volume_prompt(result: dict[str, Any]) -> str:
    summary = result_to_row(result)
    basic = result.get("basic", {})
    addresses = result.get("addresses", [])
    taxonomies = result.get("taxonomies", [])
    practice_address = format_address(get_location(addresses, "LOCATION"))
    taxonomy_summary = summarize_taxonomies(taxonomies).to_dict("records")

    return f"""
Create a practical weekly patient volume estimate for this HCP.

Important rules:
- The NPI Registry does not provide actual patient counts.
- Do not claim this is a verified patient count.
- Provide a reasonable estimated range only.
- Explain the assumptions in plain business language.
- Keep the answer concise and professional.

HCP data:
- Name: {summary["Name"]}
- NPI: {summary["NPI"]}
- Specialty: {summary["Specialty"]}
- Practice City: {summary["City"]}
- Practice State: {summary["State"]}
- Phone: {summary["Phone"]}
- Credential: {clean_text(basic.get("credential"))}
- Practice Address: {practice_address}
- Taxonomies and Licenses: {taxonomy_summary}

Return these sections:
1. Estimated Weekly Patient Volume
2. Confidence Level
3. Why This Range Is Reasonable
4. Estimated New vs. Follow-Up Patient Mix
5. Key Factors Influencing the Estimate
"""


def build_local_patient_volume_estimate(result: dict[str, Any]) -> str:
    summary = result_to_row(result)
    specialty = summary["Specialty"].lower()

    weekly_range = "60 to 120 patient visits per week"
    confidence = "Low to moderate"
    rationale = (
        "This uses a general outpatient clinic assumption of 12 to 24 visits per "
        "clinic day across 4 to 5 clinic days."
    )

    if any(term in specialty for term in ("oncology", "hematology")):
        weekly_range = "40 to 90 patient visits per week"
        rationale = (
            "Oncology and hematology visits are often longer and may include treatment "
            "planning, follow-ups, infusion coordination, and care team review."
        )
    elif any(term in specialty for term in ("primary care", "family", "internal medicine")):
        weekly_range = "80 to 140 patient visits per week"
        rationale = (
            "Primary care and general internal medicine clinics often have higher "
            "daily visit volumes with a mix of acute, follow-up, and preventive visits."
        )
    elif any(term in specialty for term in ("surgery", "surgeon", "orthopaedic")):
        weekly_range = "30 to 80 patient visits per week"
        rationale = (
            "Surgical specialists usually split time between clinic, procedures, "
            "operating room blocks, and post-operative follow-ups."
        )
    elif any(term in specialty for term in ("cardiology", "pulmonary", "gastroenterology")):
        weekly_range = "50 to 110 patient visits per week"
        rationale = (
            "Medical specialists often balance new consults, follow-ups, testing "
            "review, and procedure or hospital time."
        )
    elif any(term in specialty for term in ("dermatology", "ophthalmology", "optometry")):
        weekly_range = "90 to 180 patient visits per week"
        rationale = (
            "These specialties can have higher clinic throughput because many visits "
            "are shorter or protocol-driven."
        )
    elif any(term in specialty for term in ("psychiatry", "psychology", "mental health")):
        weekly_range = "25 to 60 patient visits per week"
        rationale = (
            "Behavioral health visits are commonly scheduled in longer appointment "
            "blocks, which reduces weekly visit volume."
        )

    return f"""
1. Estimated Weekly Patient Visits Range
{weekly_range}

2. Confidence
{confidence}

3. Why This Range Is Reasonable
{rationale}

4. Key Caveats
This is a rule-based estimate, not a verified patient count. The NPI Registry does not include patient volume, schedule density, panel size, claims data, or clinic operating days.

5. Better Data Needed To Verify
Appointment schedules, claims data, EHR encounter counts, panel size, clinic days per week, new versus follow-up mix, and procedure or hospital time.
"""


def get_estimate_service_error_message(exc: requests.exceptions.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return "The estimate service could not be reached. Showing a local estimate instead."

    if response.status_code == 429:
        return (
            "The estimate service is temporarily rate-limited. Showing a local estimate "
            "instead. Try again later or review usage limits."
        )

    if response.status_code in {401, 403}:
        return (
            "The estimate service is not configured correctly. Showing a local estimate "
            "instead."
        )

    return f"Estimate service error {response.status_code}. Showing a local estimate instead."


def estimate_weekly_patient_volume(result: dict[str, Any], api_key: str, model: str) -> str:
    payload = {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": (
                    "Estimate healthcare provider patient volume from public directory "
                    "context. Be transparent that the result is an estimate, not a "
                    "verified count."
                ),
            },
            {
                "role": "user",
                "content": build_patient_volume_prompt(result),
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        ESTIMATE_SERVICE_URL,
        headers=headers,
        json=payload,
        timeout=45,
    )
    response.raise_for_status()
    return extract_response_text(response.json())


def summarize_taxonomies(taxonomies: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for taxonomy in taxonomies:
        rows.append(
            {
                "Primary": "Yes" if taxonomy.get("primary") else "No",
                "Specialty": clean_text(taxonomy.get("desc")),
                "Taxonomy Code": clean_text(taxonomy.get("code")),
                "State": clean_text(taxonomy.get("state")),
                "License": clean_text(taxonomy.get("license")),
            }
        )
    return pd.DataFrame(rows)


def result_to_row(result: dict[str, Any]) -> dict[str, str]:
    basic = result.get("basic", {})
    taxonomies = result.get("taxonomies", [])
    primary_taxonomy = next((item for item in taxonomies if item.get("primary")), None)
    primary_taxonomy = primary_taxonomy or (taxonomies[0] if taxonomies else {})

    first_name = clean_text(basic.get("first_name"), "")
    middle_name = clean_text(basic.get("middle_name"), "")
    last_name = clean_text(basic.get("last_name"), "")
    credential = clean_text(basic.get("credential"), "")
    full_name = " ".join(part for part in (first_name, middle_name, last_name) if part)
    if credential:
        full_name = f"{full_name}, {credential}"

    addresses = result.get("addresses", [])
    practice = get_location(addresses, "LOCATION") or {}

    return {
        "Name": clean_text(full_name),
        "NPI": clean_text(result.get("number")),
        "Specialty": clean_text(primary_taxonomy.get("desc")),
        "City": clean_text(practice.get("city")),
        "State": clean_text(practice.get("state")),
        "Phone": clean_text(practice.get("telephone_number")),
    }


@st.cache_data(show_spinner=False, ttl=3600)
def search_npi_registry(
    hcp_name: str,
    npi_number: str,
    state: str,
    specialty: str,
    limit: int,
) -> dict[str, Any]:
    first_name, last_name = split_name(hcp_name)
    params: dict[str, Any] = {
        "version": "2.1",
        "enumeration_type": "NPI-1",
        "limit": limit,
    }

    if npi_number:
        params["number"] = npi_number

    if first_name and last_name:
        params["first_name"] = first_name
        params["last_name"] = last_name
    elif first_name:
        params["first_name"] = first_name

    if state:
        params["state"] = state.upper()
    if specialty:
        params["taxonomy_description"] = specialty

    response = requests.get(NPI_API_URL, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def render_hcp_details(result: dict[str, Any]) -> None:
    basic = result.get("basic", {})
    addresses = result.get("addresses", [])
    taxonomies = result.get("taxonomies", [])
    identifiers = result.get("identifiers", [])
    other_names = result.get("other_names", [])

    summary = result_to_row(result)

    st.subheader(summary["Name"])
    metric_cols = st.columns(4)
    metric_cols[0].metric("NPI", summary["NPI"])
    metric_cols[1].metric("Specialty", summary["Specialty"])
    metric_cols[2].metric("Practice State", summary["State"])
    metric_cols[3].metric("Phone", summary["Phone"])

    details_left, details_right = st.columns(2)
    with details_left:
        st.markdown("**Profile**")
        profile = {
            "Gender": clean_text(basic.get("gender")),
            "Credential": clean_text(basic.get("credential")),
            "Enumeration Date": clean_text(basic.get("enumeration_date")),
            "Last Updated": clean_text(basic.get("last_updated")),
            "Status": clean_text(basic.get("status")),
        }
        st.table(pd.DataFrame(profile.items(), columns=["Field", "Value"]))

    with details_right:
        st.markdown("**Practice Address**")
        st.info(format_address(get_location(addresses, "LOCATION")))
        st.markdown("**Mailing Address**")
        st.info(format_address(get_location(addresses, "MAILING")))

    if taxonomies:
        st.markdown("**Taxonomies And Licenses**")
        st.dataframe(summarize_taxonomies(taxonomies), width="stretch")

    if identifiers:
        st.markdown("**Other Identifiers**")
        identifier_rows = [
            {
                "Identifier": clean_text(item.get("identifier")),
                "Type": clean_text(item.get("desc")),
                "State": clean_text(item.get("state")),
                "Issuer": clean_text(item.get("issuer")),
            }
            for item in identifiers
        ]
        st.dataframe(pd.DataFrame(identifier_rows), width="stretch")

    if other_names:
        st.markdown("**Other Names**")
        name_rows = [
            {
                "Type": clean_text(item.get("type")),
                "Name": " ".join(
                    part
                    for part in (
                        clean_text(item.get("first_name"), ""),
                        clean_text(item.get("middle_name"), ""),
                        clean_text(item.get("last_name"), ""),
                    )
                    if part
                ),
            }
            for item in other_names
        ]
        st.dataframe(pd.DataFrame(name_rows), width="stretch")

    render_affiliations(summary["NPI"])

    st.markdown("**Patient Volume Estimate**")
    st.caption("Powered by gpt-4.1-mini")
    api_key = get_secret_value("OPENAI_API_KEY")
    model = get_secret_value("OPENAI_MODEL") or DEFAULT_ESTIMATE_MODEL
    estimate_key = f"patient_volume_estimate_{summary['NPI']}"

    if not api_key:
        st.warning(
            "Patient volume estimates are not configured for this deployment."
        )
    else:
        if st.button("Estimate Patient Volume", key=f"estimate_button_{summary['NPI']}"):
            with st.spinner("Creating estimate..."):
                try:
                    st.session_state[estimate_key] = estimate_weekly_patient_volume(
                        result,
                        api_key,
                        model,
                    )
                except requests.exceptions.RequestException as exc:
                    st.warning(get_estimate_service_error_message(exc))
                    st.session_state[estimate_key] = build_local_patient_volume_estimate(
                        result
                    )

        if st.session_state.get(estimate_key):
            st.info(st.session_state[estimate_key])


st.title("HCP Directory")
st.caption("Search Individual Healthcare Providers Using The Public CMS NPI Registry.")
st.write(
    "This app helps you search for healthcare providers, review their public NPI "
    "Registry profile, and generate an estimated weekly patient visit range for "
    "planning purposes."
)

with st.sidebar:
    with st.form("hcp_search_form"):
        st.header("Search Filters")
        hcp_name = st.text_input("HCP Name", placeholder="Example: Jane Smith")
        npi_number = st.text_input("NPI", placeholder="Example: 1234567890", max_chars=10)
        state = st.text_input("State", placeholder="Example: NY", max_chars=2)
        specialty = st.text_input("Specialty Contains", placeholder="Example: Oncology")
        limit = st.slider("Maximum Results", min_value=5, max_value=50, value=10, step=5)
        search_clicked = st.form_submit_button(
            "Search",
            type="primary",
            width="stretch",
        )

if "results" not in st.session_state:
    st.session_state.results = []
if "last_search" not in st.session_state:
    st.session_state.last_search = ""

npi_number = clean_npi(npi_number)
search_label = build_search_label(hcp_name, npi_number, state, specialty)

if search_clicked and npi_number and len(npi_number) != 10:
    st.warning("Enter a valid 10-digit NPI, or leave the NPI filter blank.")
    render_footer()
    st.stop()

if search_clicked and not hcp_name and not npi_number and not state and not specialty:
    st.warning("Enter at least one search filter before searching.")
    render_footer()
    st.stop()

if not hcp_name and not npi_number and not st.session_state.results:
    st.info("Enter an HCP Name or NPI in the sidebar to begin.")
    render_footer()
    st.stop()

if search_clicked:
    with st.spinner("Searching The NPI Registry..."):
        try:
            data = search_npi_registry(hcp_name, npi_number, state, specialty, limit)
        except requests.exceptions.RequestException as exc:
            st.error(f"Could Not Reach The NPI Registry API: {exc}")
            render_footer()
            st.stop()

    results = data.get("results", [])
    if not results:
        st.session_state.results = []
        st.session_state.last_search = hcp_name
        st.warning("No Matching HCPs Found. Try Fewer Filters Or Check The Spelling.")
        render_footer()
        st.stop()

    st.session_state.results = results
    st.session_state.last_search = search_label

if not st.session_state.results:
    st.info("Set Your Filters, Then Click Search.")
    render_footer()
    st.stop()

results = st.session_state.results
st.success(f"Found {len(results)} Result(s) For {st.session_state.last_search}.")
result_rows = [result_to_row(result) for result in results]
result_table = pd.DataFrame(result_rows)

st.markdown("**Matching HCPs**")
st.dataframe(result_table, width="stretch", hide_index=True)

selected_npi = st.selectbox(
    "Select An HCP To View Details",
    options=[row["NPI"] for row in result_rows],
    format_func=lambda npi: next(
        f"{row['Name']} - {row['Specialty']} - {row['NPI']}"
        for row in result_rows
        if row["NPI"] == npi
    ),
)
selected_result = next(
    result for result in results if str(result.get("number")) == selected_npi
)
render_hcp_details(selected_result)
render_footer()
