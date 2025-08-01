import streamlit as st

st.title("🧠 AI Prescription Verification")

prescription = st.text_area("Enter Prescription")
age = st.number_input("Enter Age", min_value=0)

if st.button("Analyze"):
    st.write("📋 Results will appear here...")
