
"""
RITA / EA801 - Visualizador e analisador dos Parquets (v3)

Novidades desta versao:
- aceita analisar:
    1) o dataset inteiro
    2) apenas uma frequencia com --fs
    3) um arquivo parquet especifico com --file
- mantem os graficos mais uteis para:
    * volume de dados
    * espacamento entre amostras
    * representacao temporal do sinal
    * amostras por ciclo do estimulo

Exemplos:
    python analyze_rita_v3.py --input rita_data --output rita_analysis
    python analyze_rita_v3.py --input rita_data --output analysis_1000 --fs 1000
    python analyze_rita_v3.py --output analysis_one --file "rita_data\\raw\\fs_hz=1000\\capture_xxx.parquet"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np


def parse_args():
    parser = argparse.ArgumentParser(description="Analisa os Parquets do projeto RITA.")
    parser.add_argument("--input", default="rita_data", help="Raiz do dataset.")
    parser.add_argument("--output", default="rita_analysis", help="Diretorio de saida.")
    parser.add_argument("--signal-zoom-ms", type=float, default=5.0, help="Janela curta do sinal.")
    parser.add_argument("--spacing-window-ms", type=float, default=10.0, help="Janela para mostrar espacamento.")
    parser.add_argument("--fft-max-hz", type=float, default=1000.0, help="Frequencia maxima da FFT.")
    parser.add_argument("--tone-hz", type=float, default=200.0, help="Frequencia do tom do buzzer.")
    parser.add_argument("--fs", type=int, default=None, help="Filtra uma unica taxa, ex.: --fs 1000")
    parser.add_argument("--file", default=None, help="Analisa um unico parquet especifico.")
    return parser.parse_args()


def resolve_parquet_files(input_root: Path, fs_filter=None, file_filter=None):
    if file_filter is not None:
        candidate = Path(file_filter)
        if not candidate.is_absolute():
            candidate = candidate.resolve()

        if not candidate.exists():
            raise FileNotFoundError(f"Arquivo nao encontrado: {candidate}")

        if candidate.suffix.lower() != ".parquet":
            raise ValueError(f"O arquivo precisa ser .parquet: {candidate}")

        return [candidate]

    raw_root = input_root / "raw"

    if fs_filter is None:
        parquet_files = sorted(raw_root.glob("fs_hz=*/*.parquet"))
    else:
        parquet_files = sorted((raw_root / f"fs_hz={fs_filter}").glob("*.parquet"))

    if not parquet_files:
        raise FileNotFoundError(f"Nenhum parquet encontrado em {raw_root}")

    return parquet_files


def load_all_captures(input_root: Path, fs_filter=None, file_filter=None):
    parquet_files = resolve_parquet_files(input_root, fs_filter=fs_filter, file_filter=file_filter)

    summaries = []
    data_by_file = {}

    for path in parquet_files:
        df = pd.read_parquet(path).copy()

        if df.empty:
            continue

        df = df.sort_values("sample_id").reset_index(drop=True)
        df["time_s"] = df["timestamp_us"] / 1_000_000.0
        df["time_ms"] = df["timestamp_us"] / 1_000.0
        df["dt_us"] = df["timestamp_us"].diff()

        capture_id = str(df["capture_id"].iloc[0]) if "capture_id" in df.columns else path.stem
        capture_datetime = str(df["capture_datetime"].iloc[0]) if "capture_datetime" in df.columns else path.stem
        fs_hz = int(df["fs_hz"].iloc[0])
        n_samples = len(df)

        if n_samples >= 2:
            duration_us = int(df["timestamp_us"].iloc[-1] - df["timestamp_us"].iloc[0])
            effective_fs_hz = ((n_samples - 1) / (duration_us / 1_000_000.0)) if duration_us > 0 else float("nan")
            mean_dt_us = float(df["dt_us"].dropna().mean())
            std_dt_us = float(df["dt_us"].dropna().std())
            min_dt_us = float(df["dt_us"].dropna().min())
            max_dt_us = float(df["dt_us"].dropna().max())
        else:
            duration_us = 0
            effective_fs_hz = float("nan")
            mean_dt_us = float("nan")
            std_dt_us = float("nan")
            min_dt_us = float("nan")
            max_dt_us = float("nan")

        raw_dataset_bytes = n_samples * 14

        summaries.append({
            "capture_id": capture_id,
            "capture_datetime": capture_datetime,
            "fs_hz": fs_hz,
            "n_samples": n_samples,
            "duration_us": duration_us,
            "effective_fs_hz": effective_fs_hz,
            "mean_dt_us": mean_dt_us,
            "std_dt_us": std_dt_us,
            "min_dt_us": min_dt_us,
            "max_dt_us": max_dt_us,
            "min_value": int(df["value"].min()),
            "max_value": int(df["value"].max()),
            "mean_value": float(df["value"].mean()),
            "raw_dataset_bytes": raw_dataset_bytes,
            "parquet_path": str(path),
        })

        data_by_file[str(path)] = df

    summary_df = pd.DataFrame(summaries)
    if summary_df.empty:
        raise ValueError("Nenhuma captura valida encontrada.")

    summary_df = summary_df.sort_values(["fs_hz", "capture_datetime", "capture_id"]).reset_index(drop=True)
    return summary_df, data_by_file


def choose_representative_captures(summary_df: pd.DataFrame):
    rep = (
        summary_df.sort_values(["fs_hz", "capture_datetime", "capture_id"])
        .groupby("fs_hz", as_index=False)
        .tail(1)
        .sort_values("fs_hz")
        .reset_index(drop=True)
    )
    return rep


def save_tables(summary_df: pd.DataFrame, rep_df: pd.DataFrame, output_root: Path):
    output_root.mkdir(parents=True, exist_ok=True)

    summary_df.to_csv(output_root / "capture_summary.csv", index=False)
    rep_df.to_csv(output_root / "representative_captures.csv", index=False)

    by_fs = (
        summary_df.groupby("fs_hz", as_index=False)
        .agg(
            n_captures=("capture_id", "count"),
            mean_samples=("n_samples", "mean"),
            mean_effective_fs_hz=("effective_fs_hz", "mean"),
            std_effective_fs_hz=("effective_fs_hz", "std"),
            mean_dt_us=("mean_dt_us", "mean"),
            mean_std_dt_us=("std_dt_us", "mean"),
            mean_raw_dataset_bytes=("raw_dataset_bytes", "mean"),
        )
        .sort_values("fs_hz")
    )

    by_fs.to_csv(output_root / "summary_by_fs.csv", index=False)
    return by_fs


def normalize_signal(y):
    y = np.asarray(y, dtype=float)
    std = y.std()
    if std == 0 or np.isnan(std):
        return y - y.mean()
    return (y - y.mean()) / std


def simple_fft_magnitude(values, fs_hz):
    x = values.to_numpy(dtype=float)
    x = x - x.mean()
    n = len(x)
    if n < 2:
        return np.array([]), np.array([])
    fft_vals = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs_hz)
    mag = np.abs(fft_vals)
    return freqs, mag


def plot_effective_fs(rep_df: pd.DataFrame, output_root: Path):
    plt.figure(figsize=(8, 5))
    plt.plot(rep_df["fs_hz"], rep_df["fs_hz"], marker="o", label="fs nominal")
    plt.plot(rep_df["fs_hz"], rep_df["effective_fs_hz"], marker="o", label="fs efetiva")
    plt.xlabel("fs nominal (Hz)")
    plt.ylabel("frequencia (Hz)")
    plt.title("Frequencia nominal x frequencia efetiva")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_root / "01_effective_fs.png", dpi=160)
    plt.close()


def plot_sample_count(rep_df: pd.DataFrame, output_root: Path):
    plt.figure(figsize=(8, 5))
    plt.bar(rep_df["fs_hz"].astype(str), rep_df["n_samples"])
    plt.xlabel("fs nominal (Hz)")
    plt.ylabel("numero de amostras")
    plt.title("Quantidade de amostras em 2 s")
    plt.tight_layout()
    plt.savefig(output_root / "02_sample_count.png", dpi=160)
    plt.close()


def plot_raw_volume(rep_df: pd.DataFrame, output_root: Path):
    plt.figure(figsize=(8, 5))
    kb = rep_df["raw_dataset_bytes"] / 1024.0
    plt.bar(rep_df["fs_hz"].astype(str), kb)
    plt.xlabel("fs nominal (Hz)")
    plt.ylabel("volume teorico do dataset (KB)")
    plt.title("Volume teorico produzido por captura")
    plt.tight_layout()
    plt.savefig(output_root / "03_raw_volume.png", dpi=160)
    plt.close()


def plot_sample_raster(rep_df: pd.DataFrame, data_by_file: dict, output_root: Path, spacing_window_ms: float):
    plt.figure(figsize=(10, 5))
    y_positions = list(range(len(rep_df)))

    for idx, (_, row) in enumerate(rep_df.iterrows()):
        df = data_by_file[row["parquet_path"]]
        dfw = df[df["time_ms"] <= spacing_window_ms]
        y = np.full(len(dfw), idx)
        plt.plot(dfw["time_ms"], y, linestyle="None", marker="|", markersize=12)

    plt.yticks(y_positions, [f"{int(fs)} Hz" for fs in rep_df["fs_hz"]])
    plt.xlabel("tempo (ms)")
    plt.ylabel("taxa de amostragem")
    plt.title(f"Posicoes das amostras nos primeiros {spacing_window_ms:.0f} ms")
    plt.tight_layout()
    plt.savefig(output_root / "04_sample_raster.png", dpi=160)
    plt.close()


def plot_signal_zoom_overlay(rep_df: pd.DataFrame, data_by_file: dict, output_root: Path, signal_zoom_ms: float):
    plt.figure(figsize=(10, 5))
    for _, row in rep_df.iterrows():
        df = data_by_file[row["parquet_path"]]
        dfw = df[df["time_ms"] <= signal_zoom_ms].copy()
        if dfw.empty:
            continue
        y = normalize_signal(dfw["value"].to_numpy())
        plt.plot(dfw["time_ms"], y, marker="o", linewidth=1, label=f"{int(row['fs_hz'])} Hz")
    plt.xlabel("tempo (ms)")
    plt.ylabel("amplitude normalizada")
    plt.title(f"Sinal ampliado nos primeiros {signal_zoom_ms:.0f} ms")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_root / "05_signal_zoom_overlay.png", dpi=160)
    plt.close()


def plot_dt_series(rep_df: pd.DataFrame, data_by_file: dict, output_root: Path):
    plt.figure(figsize=(10, 5))
    for _, row in rep_df.iterrows():
        df = data_by_file[row["parquet_path"]]
        dfd = df.dropna(subset=["dt_us"])
        plt.plot(dfd["sample_id"], dfd["dt_us"], label=f"{int(row['fs_hz'])} Hz")
    plt.xlabel("indice da amostra")
    plt.ylabel("delta_t (us)")
    plt.title("Intervalo entre amostras consecutivas")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_root / "06_dt_series.png", dpi=160)
    plt.close()


def plot_dt_histograms_separate(rep_df: pd.DataFrame, data_by_file: dict, output_root: Path):
    for _, row in rep_df.iterrows():
        df = data_by_file[row["parquet_path"]]
        dfd = df["dt_us"].dropna()
        plt.figure(figsize=(8, 5))
        plt.hist(dfd, bins=40)
        plt.xlabel("delta_t (us)")
        plt.ylabel("frequencia")
        plt.title(f"Distribuicao de delta_t - {int(row['fs_hz'])} Hz")
        plt.tight_layout()
        plt.savefig(output_root / f"07_dt_hist_{int(row['fs_hz'])}Hz.png", dpi=160)
        plt.close()


def plot_fft(rep_df: pd.DataFrame, data_by_file: dict, output_root: Path, fft_max_hz: float):
    plt.figure(figsize=(10, 5))
    for _, row in rep_df.iterrows():
        df = data_by_file[row["parquet_path"]]
        fs_hz = int(row["fs_hz"])
        freqs, mag = simple_fft_magnitude(df["value"], fs_hz)
        if len(freqs) == 0:
            continue
        mask = freqs <= fft_max_hz
        plt.plot(freqs[mask], mag[mask], label=f"{fs_hz} Hz")
    plt.xlabel("frequencia (Hz)")
    plt.ylabel("magnitude")
    plt.title(f"FFT do sinal (ate {fft_max_hz:.0f} Hz)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_root / "08_fft_comparison.png", dpi=160)
    plt.close()


def plot_individual_step_zoom(rep_df: pd.DataFrame, data_by_file: dict, output_root: Path, signal_zoom_ms: float):
    for _, row in rep_df.iterrows():
        df = data_by_file[row["parquet_path"]]
        fs_hz = int(row["fs_hz"])
        dfw = df[df["time_ms"] <= signal_zoom_ms].copy()
        if dfw.empty:
            continue
        y = normalize_signal(dfw["value"].to_numpy())

        plt.figure(figsize=(10, 5))
        plt.step(dfw["time_ms"], y, where="post")
        plt.plot(dfw["time_ms"], y, marker="o", linestyle="None")
        plt.xlabel("tempo (ms)")
        plt.ylabel("amplitude normalizada")
        plt.title(f"Amostras individuais - {fs_hz} Hz - primeiros {signal_zoom_ms:.0f} ms")
        plt.tight_layout()
        plt.savefig(output_root / f"09_step_zoom_{fs_hz}Hz.png", dpi=160)
        plt.close()


def plot_samples_per_period(rep_df: pd.DataFrame, output_root: Path, tone_hz: float):
    samples_per_period = rep_df["fs_hz"] / tone_hz
    plt.figure(figsize=(8, 5))
    plt.bar(rep_df["fs_hz"].astype(str), samples_per_period)
    plt.xlabel("fs nominal (Hz)")
    plt.ylabel("amostras por ciclo do tom")
    plt.title(f"Amostras por ciclo do estimulo ({tone_hz:.0f} Hz)")
    plt.tight_layout()
    plt.savefig(output_root / "10_samples_per_period.png", dpi=160)
    plt.close()


def write_report_txt(summary_df: pd.DataFrame, rep_df: pd.DataFrame, output_root: Path, signal_zoom_ms: float, spacing_window_ms: float, tone_hz: float, file_filter=None):
    lines = []
    lines.append("RITA / EA801 - Resumo da analise (v3)")
    lines.append("=" * 44)
    lines.append("")

    if file_filter is not None:
        lines.append(f"Modo: arquivo unico")
        lines.append(f"Arquivo: {Path(file_filter).resolve()}")
    else:
        lines.append("Modo: dataset/pasta")

    lines.append(f"Numero total de capturas consideradas: {len(summary_df)}")
    lines.append(f"Taxas encontradas: {sorted(summary_df['fs_hz'].unique().tolist())}")
    lines.append("")

    lines.append("Capturas representativas:")
    for _, row in rep_df.iterrows():
        lines.append(
            f"- fs={int(row['fs_hz'])} Hz | "
            f"N={int(row['n_samples'])} | "
            f"fs_efetiva={row['effective_fs_hz']:.3f} Hz | "
            f"mean_dt={row['mean_dt_us']:.3f} us | "
            f"std_dt={row['std_dt_us']:.3f} us | "
            f"arquivo={Path(row['parquet_path']).name}"
        )

    lines.append("")
    lines.append("Leitura recomendada dos graficos:")
    lines.append("- 02_sample_count.png: evidencia diretamente a quantidade de amostras.")
    lines.append("- 03_raw_volume.png: evidencia diretamente o volume de dados.")
    lines.append(f"- 04_sample_raster.png: mostra a densidade/espacamento das amostras nos primeiros {spacing_window_ms:.0f} ms.")
    lines.append(f"- 05_signal_zoom_overlay.png: mostra o mesmo trecho curto do sinal com marcadores.")
    lines.append(f"- 09_step_zoom_*.png: um grafico por frequencia, com step + pontos, para destacar a discretizacao.")
    lines.append(f"- 10_samples_per_period.png: traduz a taxa de amostragem em amostras por ciclo do tom de {tone_hz:.0f} Hz.")

    (output_root / "report_summary.txt").write_text("\n".join(lines), encoding="utf-8")


def main():
    args = parse_args()

    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    print("Lendo capturas...")
    summary_df, data_by_file = load_all_captures(
        input_root=input_root,
        fs_filter=args.fs,
        file_filter=args.file,
    )

    rep_df = choose_representative_captures(summary_df)

    print("Salvando tabelas...")
    save_tables(summary_df, rep_df, output_root)

    print("Gerando graficos...")
    plot_effective_fs(rep_df, output_root)
    plot_sample_count(rep_df, output_root)
    plot_raw_volume(rep_df, output_root)
    plot_sample_raster(rep_df, data_by_file, output_root, args.spacing_window_ms)
    plot_signal_zoom_overlay(rep_df, data_by_file, output_root, args.signal_zoom_ms)
    plot_dt_series(rep_df, data_by_file, output_root)
    plot_dt_histograms_separate(rep_df, data_by_file, output_root)
    plot_fft(rep_df, data_by_file, output_root, args.fft_max_hz)
    plot_individual_step_zoom(rep_df, data_by_file, output_root, args.signal_zoom_ms)
    plot_samples_per_period(rep_df, output_root, args.tone_hz)
    write_report_txt(
        summary_df=summary_df,
        rep_df=rep_df,
        output_root=output_root,
        signal_zoom_ms=args.signal_zoom_ms,
        spacing_window_ms=args.spacing_window_ms,
        tone_hz=args.tone_hz,
        file_filter=args.file,
    )

    print()
    print("Analise concluida.")
    print(f"Saida em: {output_root}")
    print("Arquivos principais:")
    print("- 02_sample_count.png")
    print("- 03_raw_volume.png")
    print("- 04_sample_raster.png")
    print("- 05_signal_zoom_overlay.png")
    print("- 09_step_zoom_*.png")
    print("- 10_samples_per_period.png")
    print("- report_summary.txt")


if __name__ == "__main__":
    main()
