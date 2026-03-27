#!/usr/bin/env python3
"""
Local Media Asset Manager — Phase 4: Multipage Streamlit App
Usage:
    streamlit run app.py
    # or via alias: media-search
"""
import streamlit as st
import styles

st.set_page_config(
    page_title="Media Asset Manager",
    page_icon=":material/movie:",
    layout="wide",
)
styles.inject_custom_css()

pg = st.navigation({
    "Main": [
        st.Page("pages/search_page.py", title="Search", icon=":material/search:", default=True),
        st.Page("pages/dashboard_page.py", title="Dashboard", icon=":material/dashboard:"),
    ],
})
pg.run()
