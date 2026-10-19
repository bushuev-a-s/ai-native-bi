import pandas as pd
import numpy as np
from typing import Dict, Any, List

def analyze_unknown_csv(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Полный анализ датасета с неизвестной структурой.
    Возвращает словарь с результатами по каждому типу анализа.
    """
    report = {
        "basic_stats": {},
        "duplicates": {},
        "anomalies": {},
        "correlations": {},
        "seasonality": {},
        "lags": {}
    }
    
    # --- 1. Базовая статистика и типы данных ---
    for col in df.columns:
        dtype = str(df[col].dtype)
        stats = {"dtype": dtype, "non_null": int(df[col].notna().sum())}
        
        if pd.api.types.is_numeric_dtype(df[col]):
            stats.update({
                "mean": float(df[col].mean()),
                "std": float(df[col].std()),
                "min": float(df[col].min()),
                "max": float(df[col].max()),
                "skewness": float(df[col].skew())
            })
        elif pd.api.types.is_datetime64_any_dtype(df[col]) or "date" in col.lower():
            try:
                dates = pd.to_datetime(df[col], errors="coerce")
                stats["range"] = f"{dates.min()} → {dates.max()}"
                stats["unique_days"] = int(dates.dt.date.nunique())
            except Exception:
                pass
        else:
            stats["unique_values"] = int(df[col].nunique())
            stats["top_3"] = df[col].value_counts().head(3).to_dict()
            
        report["basic_stats"][col] = stats

    # --- 2. Поиск дубликатов (как в вашем примере) ---
    dup_rows = df.duplicated().sum()
    dup_cols = [c for c in df.columns if df[c].duplicated().sum() > len(df) * 0.95]
    report["duplicates"] = {
        "duplicate_rows": int(dup_rows),
        "highly_redundant_columns": dup_cols
    }

    # --- 3. Аномалии (метод IQR для числовых столбцов) ---
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    anomalies = {}
    for col in numeric_cols:
        Q1 = df[col].quantile(0.25)
        Q3 = df[col].quantile(0.75)
        IQR = Q3 - Q1
        lower, upper = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
        mask = (df[col] < lower) | (df[col] > upper)
        if mask.any():
            anomalies[col] = {
                "count": int(mask.sum()),
                "bounds": [float(lower), float(upper)],
                "sample_outliers": df.loc[mask, col].head(5).tolist()
            }
    report["anomalies"] = anomalies

    # --- 4. Корреляции (только между числовыми столбцами) ---
    if len(numeric_cols) > 1:
        corr_matrix = df[numeric_cols].corr(method="pearson").round(3)
        # Оставляем только сильные корреляции (> 0.7 или < -0.7)
        strong_pairs = []
        for i in range(len(corr_matrix)):
            for j in range(i+1, len(corr_matrix)):
                val = corr_matrix.iloc[i, j]
                if abs(val) > 0.7:
                    strong_pairs.append({
                        "col1": corr_matrix.index[i],
                        "col2": corr_matrix.columns[j],
                        "correlation": float(val)
                    })
        report["correlations"]["strong_pairs"] = strong_pairs
        report["correlations"]["matrix_preview"] = corr_matrix.head(5).to_dict()

    # --- 5. Сезонность и Лаги (если есть временной столбец) ---
    date_col = None
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]) or "date" in c.lower() or "time" in c.lower():
            try:
                pd.to_datetime(df[c], errors="raise")
                date_col = c
                break
            except Exception:
                continue
    
    if date_col and numeric_cols:
        ts_df = df.copy()
        ts_df[date_col] = pd.to_datetime(ts_df[date_col], errors="coerce")
        ts_df = ts_df.dropna(subset=[date_col]).sort_values(date_col)
        
        # Автокорреляция (лаги) для первого числового столбца
        target = numeric_cols[0]
        lags = {}
        for lag in [1, 7, 30]:  # дневной, недельный, месячный лаг
            if len(ts_df) > lag:
                ac = ts_df[target].autocorr(lag=lag)
                lags[f"lag_{lag}"] = round(float(ac), 3) if not np.isnan(ac) else None
        report["lags"] = {"target_column": target, "values": lags}
        
        # Простая проверка сезонности (по месяцам/дням недели)
        ts_df["month"] = ts_df[date_col].dt.month
        monthly_mean = ts_df.groupby("month")[target].mean()
        seasonality_score = float(monthly_mean.std() / monthly_mean.mean()) if monthly_mean.mean() != 0 else 0
        report["seasonality"] = {
            "target_column": target,
            "monthly_variation_coeff": round(seasonality_score, 3),
            "is_seasonal": seasonality_score > 0.1  # порог субъективный
        }

    return report