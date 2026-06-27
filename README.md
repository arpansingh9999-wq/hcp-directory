# HCP Directory

This Streamlit app lets users search for healthcare providers using the public CMS NPI Registry, review public NPI profile details, and generate an estimated weekly patient visit range for planning purposes.

## Run Locally

```powershell
pip install -r requirements.txt
streamlit run hcp_directory_app.py
```

## Streamlit Secrets

Add these secrets in Streamlit Community Cloud or in a local `.streamlit/secrets.toml` file:

```toml
OPENAI_API_KEY = "your_new_openai_api_key_here"
OPENAI_MODEL = "gpt-4.1-mini"
```

Do not commit real API keys to GitHub.
