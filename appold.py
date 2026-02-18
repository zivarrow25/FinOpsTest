import streamlit as st
import pandas as pd
import re
from decimal import Decimal
import io

# --- הגדרות עיצוב ---
st.set_page_config(page_title="Eurocontrol Reconciler", layout="wide", page_icon="✈️")

# --- לוגיקת עיבוד (V6 - Dual Layer Matching & Universal Parsing) ---

def detect_charge_type(filename):
    fname = filename.upper()
    if fname.startswith('AIC'): return 'Shanwick/Oceanic'
    elif fname.startswith('M') or fname.startswith('B'): return 'Terminal/Other'
    elif fname.startswith('A'): return 'Route Charges'
    return 'Unknown'

def parse_eurocontrol_line(line_str):
    try:
        # בדיקה בסיסית לשורת נתונים
        if len(line_str) < 10 or line_str[7:9] != '01': return None
        
        line = line_str

        # 1. תאריך
        flight_date = line[9:19].replace('/', '-')
        
        # 2. מספר טיסה (Callsign) - קריטי לגיבוי
        callsign = line[25:35].split()[0].strip()

        # 3. מסלול (זיהוי חכם)
        route_match = re.search(r'([A-Z]{4}[A-Z]{4})', line[35:55])
        if route_match:
            route_block = route_match.group(1)
            dep_icao = route_block[0:4]
            arr_icao = route_block[4:8]
        else:
            dep_icao = line[38:42].strip()
            arr_icao = line[42:46].strip()

        # 4. רישום (מנגנון חכם V5)
        reg = None
        # עדיפות 1: רישום 4X או N סטנדרטי
        reg_match = re.search(r'(4X-?[A-Z]{3}|N[0-9]{1,5}[A-Z]{0,2})', line)
        
        if reg_match:
            reg = reg_match.group(1)
        else:
            # עדיפות 2: אם לא נמצא, לא ננחש סתם כדי לא לקחת "0,50" או שמות ערים.
            # נשאיר ריק כדי שמנגנון ה-Dual Layer יתפוס לפי מספר טיסה.
            reg = 'UNKNOWN'
        
        if reg:
            reg = reg.replace('-', '')
        
        # 5. סכום (מנגנון גרעיני - סריקה ימנית)
        amount = Decimal("0.00")
        amount_zone = line[35:] # סורק את כל החצי הימני
        
        # מחפש מספרים עשרוניים (עם פסיק)
        decimal_matches = re.findall(r'(\d+,\d+)', amount_zone)
        candidates = []
        for m in decimal_matches:
            val = Decimal(m.replace(',', '.'))
            if val > 0: candidates.append(val)
        
        # אם אין, מחפש שלמים
        if not candidates:
            int_matches = re.findall(r'\s(\d+)\s', amount_zone)
            for m in int_matches:
                val = Decimal(m)
                if val > 0: candidates.append(val)

        if candidates:
            amount = candidates[0]

        return {
            'euro_date': flight_date,
            'euro_callsign': callsign,
            'euro_reg': reg,
            'euro_dep': dep_icao,
            'euro_arr': arr_icao,
            'euro_amount': float(amount),
            'raw_line': line.strip()
        }
    except Exception:
        return None

# --- ממשק משתמש (UI) ---

st.title("✈️ Eurocontrol Invoice Reconciler")
st.markdown("""
מערכת התאמת חשבוניות יורוקונטרול מול נתוני Leon.
המערכת תומכת ב-Route Charges, Terminal Charges ו-Shanwick Oceanic.
""")
st.markdown("---")

# אזור העלאת קבצים
col1, col2 = st.columns(2)

with col1:
    st.header("1. Eurocontrol Files")
    uploaded_euro = st.file_uploader(
        "גרור לכאן קבצי PF (קבצי טקסט)", 
        type=['txt'], 
        accept_multiple_files=True
    )

with col2:
    st.header("2. Leon Report")
    uploaded_leon = st.file_uploader(
        "גרור לכאן את דוח לאון (Excel/CSV)", 
        type=['csv', 'xlsx', 'xls']
    )

# כפתור הפעלה
if uploaded_euro and uploaded_leon:
    if st.button("בצע התאמה (Run Matching)", type="primary"):
        
        with st.spinner('מפענח קבצים ומבצע הצלבות...'):
            # 1. עיבוד יורוקונטרול
            euro_records = []
            for uploaded_file in uploaded_euro:
                # קריאת הקובץ מהזיכרון
                stringio = io.StringIO(uploaded_file.getvalue().decode("utf-8", errors='ignore'))
                c_type = detect_charge_type(uploaded_file.name)
                
                for line in stringio:
                    parsed = parse_eurocontrol_line(line)
                    if parsed:
                        parsed['source_file'] = uploaded_file.name
                        parsed['charge_type'] = c_type
                        euro_records.append(parsed)
            
            if not euro_records:
                st.error("לא נמצאו שורות טיסה תקינות בקבצי היורוקונטרול.")
                st.stop()

            euro_df = pd.DataFrame(euro_records)
            
            # 2. עיבוד לאון
            try:
                if uploaded_leon.name.endswith('.csv'):
                    leon_df = pd.read_csv(uploaded_leon)
                else:
                    leon_df = pd.read_excel(uploaded_leon)
                
                # ניקוי עמודות
                leon_df.columns = [c.split('[')[0].strip() for c in leon_df.columns]
                leon_df['Date ADEP'] = pd.to_datetime(leon_df['Date ADEP'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
                
                # ניקוי וולידציה לעמודות קריטיות
                if 'Aircraft' in leon_df.columns:
                    leon_df['Aircraft_Clean'] = leon_df['Aircraft'].astype(str).str.replace('-', '').str.replace(' ', '')
                else:
                    st.error("שגיאה: עמודת 'Aircraft' חסרה בקובץ לאון.")
                    st.stop()
                
                if 'Flight number' in leon_df.columns:
                    leon_df['Flight_Clean'] = leon_df['Flight number'].astype(str).str.strip()
                else:
                    leon_df['Flight_Clean'] = '' # למקרה שאין מספר טיסה, ההתאמה השנייה תיכשל אבל המערכת לא תקרוס

            except Exception as e:
                st.error(f"שגיאה בקריאת קובץ לאון: {e}")
                st.stop()

            # 3. מנוע ההתאמה (Dual Layer Matching)
            
            # מפתחות שכבה 1: לפי רישום (הכי חזק)
            euro_df['KEY_REG'] = (euro_df['euro_date'] + '_' + euro_df['euro_reg'] + '_' + euro_df['euro_dep'] + '_' + euro_df['euro_arr'])
            leon_df['KEY_REG'] = (leon_df['Date ADEP'] + '_' + leon_df['Aircraft_Clean'] + '_' + leon_df['ADEP ICAO'] + '_' + leon_df['ADES ICAO'])
            
            # מפתחות שכבה 2: לפי מספר טיסה (גיבוי למקרים שאין רישום)
            euro_df['KEY_FLT'] = (euro_df['euro_date'] + '_' + euro_df['euro_callsign'] + '_' + euro_df['euro_dep'] + '_' + euro_df['euro_arr'])
            leon_df['KEY_FLT'] = (leon_df['Date ADEP'] + '_' + leon_df['Flight_Clean'] + '_' + leon_df['ADEP ICAO'] + '_' + leon_df['ADES ICAO'])
            
            # יצירת מילונים
            lookup_reg = leon_df.set_index('KEY_REG')['Trip number'].to_dict()
            lookup_flt = leon_df.set_index('KEY_FLT')['Trip number'].to_dict()

            # ביצוע ההתאמה
            # שלב א: נסה לפי רישום
            euro_df['LEON_TRIP_ID'] = euro_df['KEY_REG'].map(lookup_reg)
            
            # שלב ב: איפה שנכשלת, נסה לפי מספר טיסה
            euro_df.loc[euro_df['LEON_TRIP_ID'].isna(), 'LEON_TRIP_ID'] = euro_df['KEY_FLT'].map(lookup_flt)
            
            # תיעוד סטטוס
            euro_df['MATCH_STATUS'] = 'Unmatched'
            euro_df.loc[euro_df['LEON_TRIP_ID'].notna(), 'MATCH_STATUS'] = 'Matched'
            
            euro_df['MATCH_METHOD'] = '-'
            euro_df.loc[euro_df['KEY_REG'].map(lookup_reg).notna(), 'MATCH_METHOD'] = 'Registration'
            euro_df.loc[(euro_df['MATCH_METHOD'] == '-') & (euro_df['KEY_FLT'].map(lookup_flt).notna()), 'MATCH_METHOD'] = 'Flight Number'

            # 4. הצגת תוצאות
            matched_count = len(euro_df[euro_df['MATCH_STATUS'] == 'Matched'])
            total_count = len(euro_df)
            match_rate = (matched_count / total_count) * 100 if total_count > 0 else 0
            total_amount = euro_df['euro_amount'].sum()

            st.success("העיבוד הסתיים!")
            
            # מדדים (Metrics)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("סה\"כ שורות לתשלום", total_count)
            m2.metric("הותאמו בהצלחה", matched_count)
            m3.metric("אחוז התאמה", f"{match_rate:.1f}%", delta_color="normal" if match_rate==100 else "inverse")
            m4.metric("סכום כולל (EUR)", f"€{total_amount:,.2f}")

            # טבלה אינטראקטיבית - מציגה נתונים עיקריים
            st.subheader("פירוט הטיסות")
            
            # פונקציה לצביעת שורות
            def highlight_status(val):
                if val == 'Matched':
                    return 'background-color: #d4edda; color: black;' # ירוק בהיר
                return 'background-color: #f8d7da; color: black;' # אדום בהיר

            # תצוגה
            display_cols = ['euro_date', 'euro_callsign', 'euro_reg', 'euro_dep', 'euro_arr', 'euro_amount', 'LEON_TRIP_ID', 'MATCH_STATUS', 'MATCH_METHOD']
            st.dataframe(
                euro_df[display_cols].style.applymap(highlight_status, subset=['MATCH_STATUS']),
                use_container_width=True
            )

            # טיפול בחריגים
            unmatched_df = euro_df[euro_df['MATCH_STATUS'] == 'Unmatched']
            if not unmatched_df.empty:
                st.error(f"⚠️ ישנן {len(unmatched_df)} שורות שלא נמצאה להן התאמה!")
                with st.expander("לחץ כאן לצפייה בחריגים ובשורות המקוריות"):
                    st.write("השורות הבאות לא נמצאו בלאון (לא לפי רישום ולא לפי מספר טיסה):")
                    # מציג גם את השורה הגולמית כדי לעזור בדיבאג
                    st.dataframe(unmatched_df[display_cols + ['raw_line']])
            else:
                st.balloons() 

            # הורדת קבצים
            st.subheader("ייצוא נתונים")
            col_down1, col_down2 = st.columns(2)
            
            # המרה ל-CSV
            csv_full = euro_df.to_csv(index=False).encode('utf-8')
            
            with col_down1:
                st.download_button(
                    label="📥 הורד דו\"ח מלא (Matched)",
                    data=csv_full,
                    file_name='eurocontrol_final_report.csv',
                    mime='text/csv',
                )
            
            if not unmatched_df.empty:
                csv_unmatched = unmatched_df.to_csv(index=False).encode('utf-8')
                with col_down2:
                    st.download_button(
                        label="⚠️ הורד דו\"ח חריגים (Unmatched)",
                        data=csv_unmatched,
                        file_name='exceptions_report.csv',
                        mime='text/csv',
                    )

else:
    st.info("אנא העלה את קבצי יורוקונטרול וקובץ לאון כדי להתחיל.")
