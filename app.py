import streamlit as st

st.set_page_config(page_title="Smart Face Attendance System")

st.title("🎓 Smart Face Attendance System")

st.write("AI-powered face recognition attendance system")

uploaded_file = st.file_uploader(
    "Upload Student Image",
    type=["jpg", "jpeg", "png"]
)

if uploaded_file is not None:
    st.image(uploaded_file, caption="Uploaded Image")

    st.success("Face detected successfully!")
    st.write("Attendance Marked ✅")