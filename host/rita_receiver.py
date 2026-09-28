"""
RITA / EA801 - Receiver de aquisicoes da BitDogLab

Recebe pela USB serial as capturas enviadas pelo main.py da BitDogLab.

Fluxo:
    BitDogLab
        -> USB serial
        -> CSV temporario gravado linha a linha
        -> Parquet ao final da captura

Estrutura gerada:
    rita_data/
        raw/
            fs_hz=8000/
                capture_20260910_132500_123456.parquet
            fs_hz=4000/
                ...
        captures.csv

Dependencias:
    pip install pyserial pandas pyarrow

Uso:
    python receive_rita.py --port COM5

Linux/macOS, por exemplo:
    python receive_rita.py --port /dev/ttyACM0
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import serial


DATA_COLUMNS = [
    "sample_id",
    "timestamp_us",
    "value",
    "fs_hz",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Recebe capturas da BitDogLab e salva em Parquet."
    )

    parser.add_argument(
        "--port",
        required=True,
        help="Porta serial da BitDogLab. Ex.: COM5 ou /dev/ttyACM0",
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Baud rate. Default: 115200",
    )

    parser.add_argument(
        "--output",
        default="rita_data",
        help="Diretorio raiz do dataset. Default: rita_data",
    )

    return parser.parse_args()


def new_capture_id():
    now = datetime.now()

    return (
        now.strftime("%Y%m%d_%H%M%S_%f")
        + "_"
        + uuid.uuid4().hex[:6]
    )


def ensure_capture_index(root: Path):
    index_file = root / "captures.csv"

    if not index_file.exists():
        root.mkdir(parents=True, exist_ok=True)

        with index_file.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as f:
            writer = csv.writer(f)

            writer.writerow([
                "capture_id",
                "datetime",
                "fs_hz",
                "n_samples",
                "duration_us",
                "effective_fs_hz",
                "min_value",
                "max_value",
                "mean_value",
                "parquet_path",
            ])

    return index_file


def append_capture_index(
    index_file: Path,
    capture_id: str,
    fs_hz: int,
    df: pd.DataFrame,
    parquet_path: Path,
):
    if len(df) >= 2:
        duration_us = int(
            df["timestamp_us"].iloc[-1]
            - df["timestamp_us"].iloc[0]
        )

        if duration_us > 0:
            effective_fs = (
                (len(df) - 1)
                / (duration_us / 1_000_000.0)
            )
        else:
            effective_fs = float("nan")

    else:
        duration_us = 0
        effective_fs = float("nan")

    with index_file.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.writer(f)

        writer.writerow([
            capture_id,
            datetime.now().isoformat(timespec="seconds"),
            fs_hz,
            len(df),
            duration_us,
            effective_fs,
            int(df["value"].min()),
            int(df["value"].max()),
            float(df["value"].mean()),
            str(parquet_path),
        ])

    return effective_fs, duration_us


def extract_fs_from_status(line: str):
    """
    Entende linhas como:
        # CAPTURE_STARTED fs=8000
        # fs_hz=8000 n=16000 duration_s=2 tone_hz=200
    """

    match = re.search(
        r"(?:fs|fs_hz)\s*=\s*(\d+)",
        line
    )

    if match:
        return int(match.group(1))

    return None


def receive_capture(
    ser: serial.Serial,
    output_root: Path,
    index_file: Path,
):
    capture_id = None
    capture_fs = None

    temp_file = None
    temp_writer = None
    temp_path = None

    receiving = False
    row_count = 0

    print()
    print("Esperando captura da BitDogLab...")
    print("Pressione A na placa.")
    print()

    while True:

        raw = ser.readline()

        if not raw:
            continue

        line = raw.decode(
            "utf-8",
            errors="replace",
        ).strip()

        if not line:
            continue

        # Exibe mensagens de status da placa.
        if line.startswith("#"):
            print(line)

            detected_fs = extract_fs_from_status(line)

            if detected_fs is not None:
                capture_fs = detected_fs

        # --------------------------------------------------
        # Inicio do bloco de dados
        # --------------------------------------------------

        if line == "# BEGIN_CAPTURE":

            capture_id = new_capture_id()

            temp_dir = output_root / "_incoming"
            temp_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            temp_path = (
                temp_dir
                / f"{capture_id}.csv.tmp"
            )

            temp_file = temp_path.open(
                "w",
                newline="",
                encoding="utf-8",
                buffering=1,
            )

            temp_writer = csv.writer(temp_file)
            temp_writer.writerow(DATA_COLUMNS)

            receiving = True
            row_count = 0

            print(
                f"[PC] Recebendo captura "
                f"{capture_id}..."
            )

            continue

        # --------------------------------------------------
        # Fim da captura
        # --------------------------------------------------

        if line == "# END_CAPTURE":

            if (
                not receiving
                or temp_file is None
                or temp_path is None
            ):
                print(
                    "[PC] Aviso: END_CAPTURE recebido "
                    "sem captura aberta."
                )
                continue

            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()

            receiving = False

            # Le o CSV temporario para validar/converter.
            df = pd.read_csv(temp_path)

            if df.empty:
                print(
                    "[PC] Captura vazia. "
                    "Mantendo arquivo temporario."
                )
                continue

            # A propria linha de dados contem fs_hz;
            # ela e a fonte definitiva.
            capture_fs = int(
                df["fs_hz"].iloc[0]
            )

            # Tipos compactos e explicitos.
            df = df.astype({
                "sample_id": "uint32",
                "timestamp_us": "uint32",
                "value": "uint16",
                "fs_hz": "uint32",
            })

            # Metadados uteis para juntar varios arquivos.
            df.insert(
                0,
                "capture_id",
                capture_id,
            )

            capture_datetime = (
                datetime.now()
                .isoformat(timespec="milliseconds")
            )

            df.insert(
                1,
                "capture_datetime",
                capture_datetime,
            )

            # Dataset particionado por taxa de amostragem.
            fs_dir = (
                output_root
                / "raw"
                / f"fs_hz={capture_fs}"
            )

            fs_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            parquet_path = (
                fs_dir
                / f"capture_{capture_id}.parquet"
            )

            df.to_parquet(
                parquet_path,
                index=False,
                engine="pyarrow",
                compression="snappy",
            )

            effective_fs, duration_us = (
                append_capture_index(
                    index_file=index_file,
                    capture_id=capture_id,
                    fs_hz=capture_fs,
                    df=df,
                    parquet_path=parquet_path,
                )
            )

            # Parquet concluido; temporario deixa de ser necessario.
            temp_path.unlink(
                missing_ok=True
            )

            print()
            print("[PC] Captura salva.")
            print(
                f"     arquivo: {parquet_path}"
            )
            print(
                f"     amostras: {len(df)}"
            )
            print(
                f"     fs nominal: {capture_fs} Hz"
            )
            print(
                f"     duracao observada: "
                f"{duration_us / 1_000_000:.6f} s"
            )
            print(
                f"     fs efetiva: "
                f"{effective_fs:.3f} Hz"
            )
            print()

            print(
                "Pronto para outra captura. "
                "Mude a taxa no joystick e pressione A."
            )
            print()

            capture_id = None
            capture_fs = None
            temp_file = None
            temp_writer = None
            temp_path = None
            row_count = 0

            continue

        # --------------------------------------------------
        # Cabeçalho vindo da placa
        # --------------------------------------------------

        if line == "sample_id,timestamp_us,value,fs_hz":
            continue

        # --------------------------------------------------
        # Amostra
        # --------------------------------------------------

        if receiving and temp_writer is not None:

            parts = line.split(",")

            if len(parts) != 4:
                continue

            try:
                sample_id = int(parts[0])
                timestamp_us = int(parts[1])
                value = int(parts[2])
                fs_hz = int(parts[3])

            except ValueError:
                continue

            temp_writer.writerow([
                sample_id,
                timestamp_us,
                value,
                fs_hz,
            ])

            row_count += 1

            # Feedback sem poluir demais o terminal.
            if row_count % 2000 == 0:
                print(
                    f"[PC] {row_count} amostras recebidas..."
                )


def main():
    args = parse_args()

    output_root = Path(
        args.output
    ).resolve()

    index_file = ensure_capture_index(
        output_root
    )

    print("========================================")
    print(" RITA / EA801 - Receiver")
    print("========================================")
    print(f"Porta:   {args.port}")
    print(f"Baud:    {args.baud}")
    print(f"Dataset: {output_root}")
    print()

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            timeout=1,
        )

    except serial.SerialException as exc:
        print(
            f"Erro ao abrir {args.port}: {exc}",
            file=sys.stderr,
        )
        print(
            "\nFeche o Thonny ou qualquer outro "
            "programa que esteja usando essa COM.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Algumas placas reiniciam ao abrir a serial.
    time.sleep(2)

    try:
        receive_capture(
            ser=ser,
            output_root=output_root,
            index_file=index_file,
        )

    except KeyboardInterrupt:
        print("\nEncerrando receiver.")

    finally:
        ser.close()


if __name__ == "__main__":
    main()
