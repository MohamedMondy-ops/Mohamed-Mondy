from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import pandas as pd
import io
import json
import socket
import re
from logic import process_distribution, process_archive_logic 

app = Flask(__name__)
CORS(app) 

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@app.route('/')
def home():
    return send_file('index.html')

@app.route('/summary', methods=['POST'])
def get_summary():
    try:
        uploaded_files = request.files.getlist('files')
        excluded_text = request.form.get('excluded_numbers', '')
        
        if not uploaded_files: return jsonify({"error": "لم يتم رفع ملفات"}), 400
        dfs = [pd.read_excel(io.BytesIO(f.read())) for f in uploaded_files]
        df_raw = pd.concat(dfs, ignore_index=True)
        
        if 'requestnumber' in df_raw.columns and 'رقم الطلب' not in df_raw.columns:
            df_raw = df_raw.rename(columns={'requestnumber': 'رقم الطلب'})

        required_cols = ['رقم الطلب', 'المحافظة', 'الشركة']
        missing_cols = [col for col in required_cols if col not in df_raw.columns]
        if missing_cols: return jsonify({"error": f"الملفات تفتقد للأعمدة: {missing_cols}"}), 400

        absolute_initial_count = len(df_raw)

        if excluded_text:
            ex_list = [str(x).strip() for x in re.split(r'[,\s\n]+', excluded_text) if str(x).strip()]
            if ex_list:
                df_raw = df_raw[~df_raw['رقم الطلب'].astype(str).str.strip().isin(ex_list)]

        df = df_raw.drop_duplicates(subset=['رقم الطلب']).copy()
        
        net_count = len(df)
        duplicate_and_excluded_count = absolute_initial_count - net_count

        for col in ['الشركة', 'المحافظة', 'حالة المراجعة', 'حالة الطلب']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
                df.loc[df[col].str.lower() == 'nan', col] = None
                df.loc[df[col] == '', col] = None

        company_totals = df['الشركة'].dropna().value_counts()
        sorted_companies = company_totals.index.tolist() 

        company_stats = {}
        for comp, count in company_totals.items():
            comp_name = str(comp).strip()
            pct = round((count / net_count) * 100, 1) if net_count > 0 else 0
            company_stats[comp_name] = {"count": int(count), "percentage": pct}

        status_col = 'حالة المراجعة' if 'حالة المراجعة' in df.columns else ('حالة الطلب' if 'حالة الطلب' in df.columns else None)
        unique_govs = [x for x in df['المحافظة'].dropna().unique().tolist() if x]
        unique_statuses = [x for x in df[status_col].dropna().unique().tolist() if x] if status_col else []

        if status_col:
            summary_df = df.groupby(['الشركة', status_col]).size().reset_index(name='العدد المتاح')
            summary_df = summary_df.rename(columns={status_col: 'حالة المراجعة'})
        else:
            summary_df = df.groupby(['الشركة']).size().reset_index(name='العدد المتاح')
            summary_df['حالة المراجعة'] = 'غير محدد'
            
        summary_df['company_order'] = summary_df['الشركة'].apply(lambda x: sorted_companies.index(x) if x in sorted_companies else 999)
        summary_df = summary_df.sort_values(by=['company_order', 'العدد المتاح'], ascending=[True, False])
        summary_data = summary_df.to_dict(orient='records')

        return jsonify({ 
            "initial_count": absolute_initial_count, 
            "duplicate_count": duplicate_and_excluded_count, 
            "net_count": net_count, 
            "summary_table": summary_data, 
            "company_stats": company_stats, 
            "companies": sorted_companies, 
            "govs": unique_govs, 
            "statuses": unique_statuses 
        })
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/distribute', methods=['POST'])
def distribute_api():
    try:
        uploaded_files = request.files.getlist('files')
        files_in_memory = [io.BytesIO(f.read()) for f in uploaded_files]
        rules_json = request.form.get('rules', '[]')
        excluded_text = request.form.get('excluded_numbers', '')
        rules = json.loads(rules_json)
        mode = request.form.get('export_mode', 'assigned') 
        
        final_df, unassigned_df, errors = process_distribution(files_in_memory, rules, excluded_text)
        if errors: return "<br><br>".join(errors), 400
            
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            if mode == 'assigned':
                if final_df.empty: return "❌ لم يتم العثور على طلبات مطابقة للشروط لتصديرها.", 400
                final_df.to_excel(writer, index=False)
            else:
                if unassigned_df.empty: return "❌ لا توجد طلبات متبقية، لقد تم توزيع الخزينة بالكامل!", 400
                unassigned_df.to_excel(writer, index=False)
                
        output.seek(0)
        return send_file(output, as_attachment=True, download_name="export.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e: return str(e), 500

@app.route('/archive_summary', methods=['POST'])
def archive_summary_api():
    try:
        old_files = request.files.getlist('old_files')
        new_files = request.files.getlist('new_files')
        old_memory = [io.BytesIO(f.read()) for f in old_files if f.filename]
        new_memory = [io.BytesIO(f.read()) for f in new_files if f.filename]
        _, _, stats = process_archive_logic(old_memory, new_memory)
        if "error" in stats: return jsonify(stats), 400
        return jsonify(stats)
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/archive_export', methods=['POST'])
def archive_export_api():
    try:
        old_files = request.files.getlist('old_files')
        new_files = request.files.getlist('new_files')
        mode = request.form.get('export_mode', 'fresh') 
        old_memory = [io.BytesIO(f.read()) for f in old_files if f.filename]
        new_memory = [io.BytesIO(f.read()) for f in new_files if f.filename]
        df_fresh, df_master, stats = process_archive_logic(old_memory, new_memory)
        if "error" in stats: return stats["error"], 400
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            if mode == 'fresh':
                if df_fresh.empty: return "❌ لا يوجد أي طلبات جديدة صافية.", 400
                df_fresh.to_excel(writer, index=False)
            else:
                if df_master.empty: return "❌ حدث خطأ، لا يوجد بيانات لتصديرها.", 400
                df_master.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name="export.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e: return str(e), 500

if __name__ == '__main__':
    local_ip = get_local_ip()
    app.run(host='0.0.0.0', debug=False, port=8501)