"""Streamlit-based operator UI for the regimpact pipeline.

This package is import-safe without the ``[ui]`` extra installed. The
:mod:`streamlit_app` module imports ``streamlit`` lazily so that
``event_bridge`` and its unit tests can run in the core environment.
"""
