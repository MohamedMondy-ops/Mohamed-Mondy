import pandas as pd
import re

# =================================================================
# 1. دوال منظومة التوزيع (الأبلكيشن الأول)
# =================================================================
def process_distribution(files, rules, excluded_text=""):
    if not files: return pd.DataFrame(), pd.DataFrame(), ["❌ لم يتم رفع أي ملفات للتحليل."]
    dfs = [pd.read_excel(f) for f in files]
    df_raw = pd.concat(dfs, ignore_index=True)
    
    if 'requestnumber' in df_raw.columns and 'رقم الطلب' not in df_raw.columns:
        df_raw = df_raw.rename(columns={'requestnumber': 'رقم الطلب'})
        
    if 'رقم الطلب' not in df_raw.columns:
        return pd.DataFrame(), pd.DataFrame(), ["❌ عمود 'رقم الطلب' أو 'requestnumber' غير موجود في الملف المرفوع."]

    if excluded_text:
        ex_list = [str(x).strip() for x in re.split(r'[,\s\n]+', excluded_text) if str(x).strip()]
        if ex_list: df_raw = df_raw[~df_raw['رقم الطلب'].astype(str).str.strip().isin(ex_list)]
            
    df_output = df_raw.drop_duplicates(subset=['رقم الطلب']).copy()
    status_col = 'حالة المراجعة' if 'حالة المراجعة' in df_output.columns else ('حالة الطلب' if 'حالة الطلب' in df_output.columns else None)
    
    if status_col:
        df_output[status_col] = df_output[status_col].fillna("غير محدد").astype(str).str.strip()

    original_cols = df_output.columns.tolist()
    cols_to_keep = original_cols[:original_cols.index('المحافظة')+1] if 'المحافظة' in original_cols else original_cols.copy()
        
    must_keep = ['رقم الطلب', 'الشركة'] + ([status_col] if status_col else [])
    for c in must_keep:
        if c in original_cols and c not in cols_to_keep: cols_to_keep.append(c)
            
    df_output = df_output[cols_to_keep]
    if 'اسم المراجع' not in df_output.columns: df_output['اسم المراجع'] = None
    ordered_companies = df_output['الشركة'].value_counts().index.tolist() if 'الشركة' in df_output.columns else []

    assigned_chunks, ordered_reviewers, errors = [], [], []
    
    for rule in rules:
        reviewer = str(rule.get("اسم المراجع", "")).strip()
        company = str(rule.get("الشركة", "الكل")).strip()
        gov = str(rule.get("المحافظة", "الكل")).strip()
        status = str(rule.get("الحالة", "الكل")).strip()
        count = int(rule.get("عدد الطلبات", 0))
        
        if not reviewer or count <= 0: continue
        if reviewer not in ordered_reviewers: ordered_reviewers.append(reviewer)
            
        mask = df_output['اسم المراجع'].isnull()
        if company not in ["الكل", ""]: mask &= (df_output['الشركة'].astype(str).str.strip() == company)
        if gov not in ["الكل", ""]: mask &= (df_output['المحافظة'].astype(str).str.strip() == gov)
        if status not in ["الكل", ""]: 
            mask &= (df_output[status_col] == status) if status_col else mask
                
        matching_indices = df_output[mask].index
        available = len(matching_indices)
        
        if available == 0: 
            errors.append(f"❌ المراجع (<b>{reviewer}</b>): لا يوجد طلبات من (<b>{company}</b>) | (<b>{gov}</b>) | (<b>{status}</b>).")
            continue
        elif available < count: 
            errors.append(f"⚠️ المراجع (<b>{reviewer}</b>): طلبت (<b>{count}</b>) من (<b>{company}</b>)، المتاح هو (<b>{available}</b>) فقط.")
            continue
            
        actual_to_assign = min(count, available)
        if actual_to_assign > 0:
            # =================================================================
            # التعديل العبقري: التوازن الناعم (Soft Balancing) لحالات المراجعة
            # =================================================================
            if status == "الكل" and status_col:
                subset_df = df_output.loc[matching_indices]
                status_counts = subset_df[status_col].value_counts()
                
                allocations = {}
                remainders = []
                # حساب النسبة والتناسب لكل حالة في الخزنة المتاحة للشركة دي
                for stat, stat_avail in status_counts.items():
                    exact = actual_to_assign * (stat_avail / available)
                    base = int(exact)
                    allocations[stat] = base
                    remainders.append({"stat": stat, "rem": exact - base, "avail": stat_avail})
                
                needed = actual_to_assign - sum(allocations.values())
                remainders.sort(key=lambda x: x["rem"], reverse=True)
                
                # جبر الكسور عشان التوزيع يقفل على العدد المطلوب بالظبط
                for item in remainders:
                    if needed > 0 and allocations[item["stat"]] < item["avail"]:
                        allocations[item["stat"]] += 1
                        needed -= 1
                        
                assigned_indices = []
                for stat, assign_count in allocations.items():
                    if assign_count > 0:
                        stat_indices = subset_df[subset_df[status_col] == stat].index[:assign_count].tolist()
                        assigned_indices.extend(stat_indices)
            else:
                assigned_indices = matching_indices[:actual_to_assign].tolist()
            
            df_output.loc[assigned_indices, 'اسم المراجع'] = reviewer
            assigned_chunks.append(df_output.loc[assigned_indices].copy())
            
    if errors: return pd.DataFrame(), pd.DataFrame(), errors
        
    unassigned_df = df_output[df_output['اسم المراجع'].isnull()].copy()
    if 'اسم المراجع' in unassigned_df.columns: unassigned_df = unassigned_df.drop(columns=['اسم المراجع'])
    
    if assigned_chunks:
        final_df = pd.concat(assigned_chunks, ignore_index=True)
        final_df['اسم المراجع'] = pd.Categorical(final_df['اسم المراجع'], categories=ordered_reviewers, ordered=True)
        if 'الشركة' in final_df.columns and ordered_companies: final_df['الشركة'] = pd.Categorical(final_df['الشركة'], categories=ordered_companies, ordered=True)
            
        sort_cols = [c for c in ['اسم المراجع', 'الشركة', status_col, 'المحافظة'] if c and c in final_df.columns]
        if sort_cols: final_df = final_df.sort_values(by=sort_cols)

        preferred_order = ['رقم الطلب', 'الشركة', 'اسم المراجع'] + ([status_col] if status_col else []) + ['المحافظة']
        first_cols = [c for c in preferred_order if c in final_df.columns]
        final_df = final_df[first_cols + [c for c in final_df.columns if c not in first_cols]]
        
        if not unassigned_df.empty:
            sort_cols_un = []
            if 'الشركة' in unassigned_df.columns and ordered_companies: 
                unassigned_df['الشركة'] = pd.Categorical(unassigned_df['الشركة'], categories=ordered_companies, ordered=True)
                sort_cols_un.append('الشركة')
            if status_col and status_col in unassigned_df.columns: sort_cols_un.append(status_col)
            if 'المحافظة' in unassigned_df.columns: sort_cols_un.append('المحافظة')
            if sort_cols_un: unassigned_df = unassigned_df.sort_values(by=sort_cols_un)
            
            preferred_order_un = ['رقم الطلب', 'الشركة'] + ([status_col] if status_col else []) + ['المحافظة']
            first_cols_un = [c for c in preferred_order_un if c in unassigned_df.columns]
            unassigned_df = unassigned_df[first_cols_un + [c for c in unassigned_df.columns if c not in first_cols_un]]
            
        return final_df, unassigned_df, []
    else:
        return pd.DataFrame(), pd.DataFrame(), ["❌ لم يتم توزيع أي طلبات، يرجى مراجعة جدول المدخلات."]

# =================================================================
# 2. دوال (الأبلكيشن الثاني Layout)
# =================================================================
def process_archive_logic(old_files, new_files):
    def standardize_layout_cols(df):
        col_map = {}
        for c in df.columns:
            c_lower = str(c).lower().strip()
            if c_lower in ['requestnumber', 'رقم الطلب', 'رقم طلب']: col_map[c] = 'رقم الطلب'
            elif c_lower in ['gehat_wlaya', 'جهة الولاية', 'جهه الولايه']: col_map[c] = 'جهة الولاية'
            elif c_lower in ['geht_tanseeq', 'التنسيق', 'جهة التنسيق', 'جهه التنسيق']: col_map[c] = 'التنسيق'
            elif c_lower in ['name_city', 'المدينة', 'مدينة']: col_map[c] = 'المدينة'
        df = df.rename(columns=col_map)
        for req_c in ['رقم الطلب', 'جهة الولاية', 'التنسيق', 'المدينة']:
            if req_c not in df.columns: df[req_c] = None
        return df[['رقم الطلب', 'جهة الولاية', 'التنسيق', 'المدينة']]

    if old_files:
        old_dfs = [pd.read_excel(f) for f in old_files]
        df_old = pd.concat(old_dfs, ignore_index=True)
        df_old = standardize_layout_cols(df_old)
        df_old = df_old.dropna(subset=['رقم الطلب'])
        df_old = df_old.drop_duplicates(subset=['رقم الطلب'])
    else:
        df_old = pd.DataFrame(columns=['رقم الطلب', 'جهة الولاية', 'التنسيق', 'المدينة'])

    if new_files:
        new_dfs = [pd.read_excel(f) for f in new_files]
        df_new = pd.concat(new_dfs, ignore_index=True)
        df_new = standardize_layout_cols(df_new)
        df_new = df_new.dropna(subset=['رقم الطلب'])
        initial_new_count = len(df_new)
        df_new = df_new.drop_duplicates(subset=['رقم الطلب'])
        duplicates_in_new = initial_new_count - len(df_new)
    else:
        return None, None, {"error": "❌ لم يتم رفع شيت جديد لفحصه."}

    if not df_old.empty: df_new_clean = df_new[~df_new['رقم الطلب'].astype(str).str.strip().isin(df_old['رقم الطلب'].astype(str).str.strip())].copy()
    else: df_new_clean = df_new.copy()

    if not df_old.empty and not df_new_clean.empty: df_master = pd.concat([df_old, df_new_clean], ignore_index=True)
    elif df_old.empty: df_master = df_new_clean.copy()
    else: df_master = df_old.copy()

    if not df_new_clean.empty: df_new_clean = df_new_clean.sort_values(by=['جهة الولاية'])
    if not df_master.empty: df_master = df_master.sort_values(by=['جهة الولاية'])

    stats = {
        "old_archive_count": len(df_old), "new_uploaded_count": len(df_new) + duplicates_in_new, "duplicates_in_new": duplicates_in_new,
        "found_in_old": len(df_new) - len(df_new_clean), "fresh_clean_count": len(df_new_clean), "total_master_count": len(df_master), "welaya_stats": []
    }

    if not df_new_clean.empty:
        w_stats = df_new_clean['جهة الولاية'].fillna('غير محدد').value_counts().reset_index()
        w_stats.columns = ['جهة الولاية', 'العدد']
        stats["welaya_stats"] = w_stats.to_dict(orient='records')

    return df_new_clean, df_master, stats