# LExport

A multi-tool Streamlit app for converting lexical XML dictionaries into
various output formats. Upload your XML dictionary once, then convert in any of the available formats.

## Available converters

| Converter | Status | Description |
|-----------|--------|-------------|
| Wiktionary | ✅  | Wiktionary copiable wikitext|
| NooJ | ✅  | NooJ `.dic` dictionary format |
| DIG4EL | ✅ | `.json` parallel corpus of examples |
| EvoSem | ✅ | `.tsv` table for EvoSem |

## Deployment Notice

This project is deployed on **Streamlit Community Cloud**. Because Streamlit Community Cloud automatically puts inactive applications to sleep after a period of inactivity, the application may occasionally be unavailable if it has not been accessed recently.

If the deployed application is unavailable during evaluation, it can be easily redeployed by:

1. Opening the project in Streamlit Community Cloud (https://streamlit.io/).
2. Selecting this GitHub repository.
3. Deploying the application again.

Alternatively, the application can be run locally by cloning the repository and executing:

```bash
pip install -r requirements.txt
streamlit run app.py
```
