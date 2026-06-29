import os
import re
from typing import Any

import pandas as pd
import requests
import streamlit as st


NPI_API_URL = "https://npiregistry.cms.hhs.gov/api/"
ESTIMATE_SERVICE_URL = "https://api.openai.com/v1/responses"
DEFAULT_ESTIMATE_MODEL = "gpt-4.1-mini"


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


def extract_response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return clean_text(data["output_text"])
