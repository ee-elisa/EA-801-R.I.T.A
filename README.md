<p align="center">
  <img src="docs/images/rita-logo.png" width="300">
</p>

# RITA

**RITA — Restrição e Implicação da Taxa de Amostragem.**

Experimento em sistemas embarcados para investigar o impacto de diferentes taxas de amostragem afetam a representação do sinal, volume e fluxo de dados.

## Overview

RITA é uma projeto de sistemas embarcados desenvolvido usando a plataforma  BitDogLab.

O sistema adquire dados em diferentes taxas de amostragem e performa tanto a aquisição quanto armazena em buffer e transmite as amostras coletadas a um outro dispositivo, no contexto do experimento o computador energizando a placa, permitindo armazenamento e analise dos dados

As principais questoes que motivam o projeto são:

> Como a taxa de amostragem afeta a quantidade de informação disponível


### Embedded system

The firmware is implemented in MicroPython and is responsible for:

- Aquisição dos dados via sensor;
- temporização da amostragem;
- comfiguração do tempo desejado;
- buffering dos dados;
- visualização do OLED ;
- interface do usuário;
- transmissao via USB;
- Analise inicial on board.

Code: [`firmware/`](firmware/)

### Host-side processing

Scripts rodando na máquina host permitem:

- Aquisição e armazenamento;
- geração de dataset;
- Conversão dos dados em CSV/Parquet;
- Visualização do sinal;
- Analise aprimorada.
## Diagram
```mermaid

flowchart LR
    %% =========================
    %% RITA - CURRENT PIPELINE
    %% =========================

    subgraph BOARD["BitDogLab / MicroPython"]
        direction TB

        USER["User controls<br/>Joystick Y → sampling rate<br/>Button A → capture<br/>Button B → OLED page"]

        CONFIG["Configure experiment<br/>fs = 1 / 2 / 4 / 8 kHz<br/>duration = 2 s<br/>tone = 200 Hz"]

        TONE["Buzzer<br/>200 Hz reference tone"]

        ADC["Microphone ADC"]

        SAMPLE["Timed acquisition loop<br/>wait for target timestamp<br/>read ADC<br/>store sample + timestamp"]

        BUFFER["RAM buffers<br/>ADC values: uint16<br/>timestamps: uint32"]

        METRICS["Temporal analysis<br/>effective fs<br/>mean Δt<br/>jitter<br/>max lateness"]

        UI["Local feedback<br/>OLED<br/>5×5 LED matrix<br/>RGB LED"]

        SERIAL["USB CDC serial<br/>CSV-formatted stream"]

        USER --> CONFIG
        CONFIG --> TONE
        TONE --> ADC
        CONFIG --> SAMPLE
        ADC --> SAMPLE
        SAMPLE --> BUFFER
        BUFFER --> METRICS
        METRICS --> UI
        BUFFER --> SERIAL
    end

    subgraph PC["Host PC / Python"]
        direction TB

        RX["rita_receiver.py<br/>PySerial"]

        TEMP["Temporary CSV<br/>written line by line"]

        DF["Pandas DataFrame<br/>validation + typed columns"]

        PARQUET["Parquet capture<br/>Snappy compression"]

        PARTITION["Dataset partition<br/>raw/fs_hz=1000/<br/>raw/fs_hz=2000/<br/>raw/fs_hz=4000/<br/>raw/fs_hz=8000/"]

        INDEX["captures.csv<br/>capture metadata + summary"]

        RX --> TEMP
        TEMP --> DF
        DF --> PARQUET
        PARQUET --> PARTITION
        DF --> INDEX
    end

    SERIAL -->|"USB serial"| RX
```
```
