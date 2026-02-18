import streamlit as st
import pandas as pd
import re
from decimal import Decimal
import io

# --- הגדרות עמוד ותצוגה ---
st.set_page_config(page_title="Aviation Invoice Auditor", layout="wide", page_icon="✈️")

# CSS ליישור הטבלאות לשמאל
st.markdown("""
<style>
    .dataframe {text-align: left !important;}
    th {text-align: left !important;}
</style>
""", unsafe_allow_html=True)

# --- פונקציות לוגיקה ועיבוד ---

def extract_invoice_reference(content):
    """
    מחלץ את מספר החשבונית מתוך תוכן הקובץ באמצעות Regex.
    מחפש תבנית של: XX/XXXXXX/XX (למשל: 31/037959/32)
    """
    # קורא רק את השורות הראשונות (הכותרת נמצאת תמיד בהתחלה)
    lines = content.splitlines()[:3]
    
    for line in lines:
        # Regex Pattern:
        # \d{2}   -> 2 ספרות (קוד מדינה)
        # /       -> סלאש
        # \d{5,12}-> בין 5 ל-12 ספרות (מספר חשבונית)
        # /       -> סלאש
        # \d{2}   -> 2 ספרות (קוד משתמש)
        match = re.search(r'(\d{2}/\d{5,12}/\d{2})', line)
        if match:
            return match.group(1) # מחזיר רק את ה-31/037959/32
            
    return "UNKNOWN_REF"

def parse_eurocontrol_line(line_str):
    try:
        if len(line_str) < 10: return None
        if isinstance(line_str, bytes):
            line = line_str.decode('utf-8', errors='ignore')
        else:
            line = line_str
            
        # בדיקה שזו שורת טיסה (מתחילה ב-01 בדרך כלל בקבצי PF)
        if line[7:9] != '01': return None

        # חילוץ נתונים לפי מיקומים סטנדרטיים
        flight_date = line[9:19].replace('/', '-')
        callsign = line[25:35].split()[0].strip()

        # זיהוי מסלול (Dep/Arr)
        route_match = re.search(r'([A-Z]{4}[A-Z]{4})', line[35:55])
        if route_match:
            route_block = route_match.group(1)
            dep_icao = route_block[0:4]
            arr_icao = route_block[4:8]
        else:
            dep_icao = line[38:42].strip()
            arr_icao = line[42:46].strip()

        # זיהוי רישום (Reg)
        reg = None
        reg_match = re.search(r'(4X-?[A-Z]{3}|N[0-9]{1,5}[A-Z]{0,2})', line)
        if reg_match:
            reg = reg_match.group(1).replace('-', '')
        else:
            reg = 'UNKNOWN'

        # זיהוי סכום (Amount)
        amount = Decimal("0.00")
        amount_zone = line[35:]
        decimal_matches = re.findall(r'(\d+,\d+)', amount_zone)
        candidates = []
        for m in decimal_matches:
            val = Decimal(m.replace(',', '.'))
            if val > 0: candidates.append(val)
        
        if not candidates:
            int_matches = re.findall(r'\s(\d+)\s', amount_zone)
            for m in int_matches:
                val = Decimal(m)
                if val > 0: candidates.append(val)
        if candidates:
            amount = candidates[0]

        return {
            'Date': flight_date,
            'Callsign': callsign,
            'Reg': reg,
            'Dep': dep_icao,
            'Arr': arr_icao,
            'Amount': float(amount),
            'Raw_Line': line.strip()
        }
    except Exception:
        return None

def generate_excel(df_main, df_unmatched):
    """מייצר קובץ אקסל אחד עם שני גיליונות"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # גיליון 1: Main Report
        df_main.to_excel(writer, sheet_name='Main Report', index=False)
        
        # גיליון 2: Unmatched
        if not df_unmatched.empty:
            df_unmatched.to_excel(writer, sheet_name='Unmatched Investigation', index=False)
            
    return output.getvalue()

# --- ממשק משתמש (UI) ---

st.title("✈️ Aviation Invoice Auditor")
st.markdown("Reconcile Eurocontrol Invoices against Leon Data.")
st.markdown("---")

col1, col2 = st.columns(2)
with col1:
    uploaded_euro = st.file_uploader("1. Eurocontrol Files (TXT)", type=['txt'], accept_multiple_files=True)
with col2:
    uploaded_leon = st.file_uploader("2. Leon Report (Excel/CSV)", type=['csv', 'xlsx', 'xls'])

if uploaded_euro and uploaded_leon:
    if st.button("RUN AUDIT 🚀", type="primary"):
        with st.spinner('Parsing Invoice & Matching Flights...'):
            
            # 1. עיבוד יורוקונטרול
            euro_records = []
            
            for uploaded_file in uploaded_euro:
                # קריאת תוכן הקובץ לזיכרון
                content = uploaded_file.getvalue().decode("utf-8", errors='ignore')
                
                # --- שלב קריטי: חילוץ מספר החשבונית מהתוכן ---
                invoice_ref = extract_invoice_reference(content)
                
                # מעבר על השורות וחיפוש טיסות
                for line in content.splitlines():
                    parsed = parse_eurocontrol_line(line)
                    if parsed:
                        # הצמדת מספר החשבונית שמצאנו לכל טיסה בקובץ הזה
                        parsed['Invoice No'] = invoice_ref
                        parsed['Source File'] = uploaded_file.name
                        euro_records.append(parsed)
            
            if not euro_records:
                st.error("No valid flight lines found inside the uploaded text files.")
                st.stop()

            euro_df = pd.DataFrame(euro_records)

            # 2. עיבוד לאון
            try:
                if uploaded_leon.name.endswith('.csv'):
                    try:
                        leon_df = pd.read_csv(uploaded_leon)
                    except UnicodeDecodeError:
                        leon_df = pd.read_csv(uploaded_leon, encoding='latin1')
                else:
                    leon_df = pd.read_excel(uploaded_leon)
                
                # ניקוי עמודות
                leon_df.columns = [c.split('[')[0].strip() for c in leon_df.columns]
                leon_df['Date ADEP'] = pd.to_datetime(leon_df['Date ADEP'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
                
                if 'Aircraft' in leon_df.columns:
                    leon_df['Aircraft_Clean'] = leon_df['Aircraft'].astype(str).str.replace('-', '').str.replace(' ', '')
                else:
                    st.error("Missing 'Aircraft' column in Leon file.")
                    st.stop()
                
                if 'Flight number' in leon_df.columns:
                    leon_df['Flight_Clean'] = leon_df['Flight number'].astype(str).str.strip()
                else:
                    leon_df['Flight_Clean'] = ''

            except Exception as e:
                st.error(f"Error reading Leon file: {e}")
                st.stop()

            # 3. מנוע ההתאמה
            euro_df['KEY_REG'] = (euro_df['Date'] + '_' + euro_df['Reg'] + '_' + euro_df['Dep'] + '_' + euro_df['Arr'])
            leon_df['KEY_REG'] = (leon_df['Date ADEP'] + '_' + leon_df['Aircraft_Clean'] + '_' + leon_df['ADEP ICAO'] + '_' + leon_df['ADES ICAO'])
            
            euro_df['KEY_FLT'] = (euro_df['Date'] + '_' + euro_df['Callsign'] + '_' + euro_df['Dep'] + '_' + euro_df['Arr'])
            leon_df['KEY_FLT'] = (leon_df['Date ADEP'] + '_' + leon_df['Flight_Clean'] + '_' + leon_df['ADEP ICAO'] + '_' + leon_df['ADES ICAO'])
            
            lookup_reg = leon_df.set_index('KEY_REG')['Trip number'].to_dict()
            lookup_flt = leon_df.set_index('KEY_FLT')['Trip number'].to_dict()

            euro_df['Leon Trip Number'] = euro_df['KEY_REG'].map(lookup_reg)
            euro_df.loc[euro_df['Leon Trip Number'].isna(), 'Leon Trip Number'] = euro_df['KEY_FLT'].map(lookup_flt)
            
            euro_df['Matched?'] = 'NO'
            euro_df.loc[euro_df['Leon Trip Number'].notna(), 'Matched?'] = 'YES'
            
            euro_df['Match Method'] = '-'
            euro_df.loc[euro_df['KEY_REG'].map(lookup_reg).notna(), 'Match Method'] = 'Registration'
            euro_df.loc[(euro_df['Match Method'] == '-') & (euro_df['KEY_FLT'].map(lookup_flt).notna()), 'Match Method'] = 'Flight Number'

            # 4. הכנת הטבלה הסופית
            final_columns = [
                'Invoice No', 
                'Date', 
                'Reg', 
                'Dep', 
                'Arr', 
                'Amount', 
                'Leon Trip Number', 
                'Matched?', 
                'Match Method'
            ]
            
            df_display = euro_df[final_columns].copy()
            
            # הכנת חריגים
            df_unmatched = euro_df[euro_df['Matched?'] == 'NO'].copy()
            # מוסיפים את השורה הגולמית לגיליון חריגים בלבד
            unmatched_export_cols = final_columns + ['Raw_Line']
            df_unmatched_export = df_unmatched[unmatched_export_cols]

            # 5. הצגת דשבורד
            st.success("Analysis Completed Successfully.")
            
            # חישוב מדדים
            total_flights = len(euro_df)
            matched_flights = len(euro_df[euro_df['Matched?'] == 'YES'])
            match_rate = (matched_flights / total_flights) * 100 if total_flights > 0 else 0
            total_amount = euro_df['Amount'].sum()

            # --- מיני דשבורד (הוחזר!) ---
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Flights", total_flights)
            m2.metric("Total Amount", f"€{total_amount:,.2f}") # סה"כ לתשלום
            m3.metric("Matched Flights", matched_flights)
            m4.metric("Match Rate", f"{match_rate:.1f}%")

            # תצוגת טבלה ראשית
            st.subheader("Invoice Details")
            
            def color_row(row):
                return ['background-color: #d4edda'] * len(row) if row['Matched?'] == 'YES' else ['background-color: #f8d7da'] * len(row)

            st.dataframe(
                df_display.style.apply(color_row, axis=1),
                use_container_width=True,
                hide_index=True
            )
            
            if not df_unmatched.empty:
                st.warning(f"Found {len(df_unmatched)} unmatched flights.")
                with st.expander("Show Unmatched Details"):
                    st.dataframe(df_unmatched_export, hide_index=True)

            # 6. הורדת קובץ אקסל אחד
            excel_data = generate_excel(df_display, df_unmatched_export)
            
            st.download_button(
                label="📥 Download Full Audit Report (Excel)",
                data=excel_data,
                file_name='Audit_Report_Final.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )

else:
    st.info("Please upload Eurocontrol (TXT) and Leon (Excel/CSV) files to begin.")
