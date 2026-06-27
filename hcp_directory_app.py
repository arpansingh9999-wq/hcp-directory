import os
import re
from typing import Any

import pandas as pd
import requests
import streamlit as st


NPI_API_URL = "https://npiregistry.cms.hhs.gov/api/"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


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
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


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
    st.caption("Weekend Project By Arpan S")


def get_secret_value(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value).strip() or os.getenv(name, "").strip()


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
Create A Practical Weekly Patient Volume Estimate For This HCP.

Important Rules:
- The NPI Registry Does Not Provide Actual Patient Counts.
- Do Not Claim This Is A Verified Patient Count.
- Provide A Reasonable Estimated Range Only.
- Explain The Assumptions In Plain Business Language.
- Keep The Answer Concise And In Proper Case.

HCP Data:
- Name: {summary["Name"]}
- NPI: {summary["NPI"]}
- Specialty: {summary["Specialty"]}
- Practice City: {summary["City"]}
- Practice State: {summary["State"]}
- Phone: {summary["Phone"]}
- Credential: {clean_text(basic.get("credential"))}
- Practice Address: {practice_address}
- Taxonomies And Licenses: {taxonomy_summary}

Return These Sections:
1. Estimated Weekly Patient Visits Range
2. Confidence
3. Why This Range Is Reasonable
4. Key Caveats
5. Better Data Needed To Verify
"""


def build_local_patient_volume_estimate(result: dict[str, Any]) -> str:
    summary = result_to_row(result)
    specialty = summary["Specialty"].lower()

    weekly_range = "60 To 120 Patient Visits Per Week"
    confidence = "Low To Moderate"
    rationale = (
        "This Uses A General Outpatient Clinic Assumption Of 12 To 24 Visits Per "
        "Clinic Day Across 4 To 5 Clinic Days."
    )

    if any(term in specialty for term in ("oncology", "hematology")):
        weekly_range = "40 To 90 Patient Visits Per Week"
        rationale = (
            "Oncology And Hematology Visits Are Often Longer, Include Treatment "
            "Planning, Follow-Ups, Infusion Coordination, And Care Team Review."
        )
    elif any(term in specialty for term in ("primary care", "family", "internal medicine")):
        weekly_range = "80 To 140 Patient Visits Per Week"
        rationale = (
            "Primary Care And General Internal Medicine Clinics Often Have Higher "
            "Daily Visit Volumes With A Mix Of Acute, Follow-Up, And Preventive Visits."
        )
    elif any(term in specialty for term in ("surgery", "surgeon", "orthopaedic")):
        weekly_range = "30 To 80 Patient Visits Per Week"
        rationale = (
            "Surgical Specialists Usually Split Time Between Clinic, Procedures, "
            "Operating Room Blocks, And Post-Operative Follow-Ups."
        )
    elif any(term in specialty for term in ("cardiology", "pulmonary", "gastroenterology")):
        weekly_range = "50 To 110 Patient Visits Per Week"
        rationale = (
            "Medical Specialists Often Balance New Consults, Follow-Ups, Testing "
            "Review, And Procedure Or Hospital Time."
        )
    elif any(term in specialty for term in ("dermatology", "ophthalmology", "optometry")):
        weekly_range = "90 To 180 Patient Visits Per Week"
        rationale = (
            "These Specialties Can Have Higher Clinic Throughput Because Many Visits "
            "Are Shorter Or Protocol-Driven."
        )
    elif any(term in specialty for term in ("psychiatry", "psychology", "mental health")):
        weekly_range = "25 To 60 Patient Visits Per Week"
        rationale = (
            "Behavioral Health Visits Are Commonly Scheduled In Longer Appointment "
            "Blocks, Which Reduces Weekly Visit Volume."
        )

    return f"""
1. Estimated Weekly Patient Visits Range
{weekly_range}

2. Confidence
{confidence}

3. Why This Range Is Reasonable
{rationale}

4. Key Caveats
This Is A Local Rule-Based Estimate, Not A Verified Patient Count. The NPI Registry Does Not Include Patient Volume, Schedule Density, Panel Size, Claims Data, Or Clinic Operating Days.

5. Better Data Needed To Verify
Appointment Schedules, Claims Data, EHR Encounter Counts, Panel Size, Clinic Days Per Week, New Versus Follow-Up Mix, And Procedure Or Hospital Time.
"""


def get_openai_error_message(exc: requests.exceptions.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return "Could Not Reach The OpenAI API. Showing A Local Estimate Instead."

    if response.status_code == 429:
        return (
            "OpenAI Rate Limit Or Quota Was Reached. Showing A Local Estimate Instead. "
            "Try Again Later, Check Billing, Or Use A Lower-Cost Model Such As gpt-4.1-nano."
        )

    if response.status_code in {401, 403}:
        return (
            "OpenAI API Key Could Not Be Authorized. Showing A Local Estimate Instead. "
            "Check That OPENAI_API_KEY Is Valid And Has Access."
        )

    return f"OpenAI API Error {response.status_code}. Showing A Local Estimate Instead."


def estimate_weekly_patient_volume(result: dict[str, Any], api_key: str, model: str) -> str:
    payload = {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": (
                    "You Estimate Healthcare Provider Patient Volume From Public "
                    "Directory Context. You Must Be Transparent That The Result Is "
                    "An Estimate, Not A Verified Count."
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
        OPENAI_RESPONSES_URL,
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
        st.dataframe(summarize_taxonomies(taxonomies), use_container_width=True)

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
        st.dataframe(pd.DataFrame(identifier_rows), use_container_width=True)

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
        st.dataframe(pd.DataFrame(name_rows), use_container_width=True)

    st.markdown("**Patient Volume Estimate**")
    st.caption("This is an estimated range, not a verified patient count.")
    api_key = get_secret_value("OPENAI_API_KEY")
    model = get_secret_value("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    estimate_key = f"patient_volume_estimate_{summary['NPI']}"

    if not api_key:
        st.warning(
            "Add OPENAI_API_KEY As A Streamlit Secret Or Environment Variable To Enable This Feature."
        )
        st.code('OPENAI_API_KEY = "Your_New_Rotated_Key_Here"', language="toml")
    else:
        if st.button("Estimate Weekly Patient Volume", key=f"estimate_button_{summary['NPI']}"):
            with st.spinner("Creating AI Estimate..."):
                try:
                    st.session_state[estimate_key] = estimate_weekly_patient_volume(
                        result,
                        api_key,
                        model,
                    )
                except requests.exceptions.RequestException as exc:
                    st.warning(get_openai_error_message(exc))
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
        state = st.text_input("State", placeholder="Example: NY", max_chars=2)
        specialty = st.text_input("Specialty Contains", placeholder="Example: Oncology")
        limit = st.slider("Maximum Results", min_value=5, max_value=50, value=10, step=5)
        search_clicked = st.form_submit_button(
            "Search",
            type="primary",
            use_container_width=True,
        )

if "results" not in st.session_state:
    st.session_state.results = []
if "last_search" not in st.session_state:
    st.session_state.last_search = ""

if search_clicked and not hcp_name:
    st.warning("Enter An HCP Name Before Searching.")
    render_footer()
    st.stop()

if not hcp_name and not st.session_state.results:
    st.info("Enter An HCP Name In The Sidebar To Begin.")
    render_footer()
    st.stop()

if search_clicked:
    with st.spinner("Searching The NPI Registry..."):
        try:
            data = search_npi_registry(hcp_name, state, specialty, limit)
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
    st.session_state.last_search = hcp_name

if not st.session_state.results:
    st.info("Set Your Filters, Then Click Search.")
    render_footer()
    st.stop()

results = st.session_state.results
st.success(f"Found {len(results)} Result(s) For {st.session_state.last_search}.")
result_rows = [result_to_row(result) for result in results]
result_table = pd.DataFrame(result_rows)

st.markdown("**Matching HCPs**")
st.dataframe(result_table, use_container_width=True, hide_index=True)

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
