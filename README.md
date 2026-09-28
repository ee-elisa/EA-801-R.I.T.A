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
``` mermaid
flowchart TB

    %% =========================
    %% BITDOGLAB
    %% =========================

    subgraph BOARD["BitDogLab / MicroPython"]
        direction TB

        A["User selects sampling rate<br/>1 / 2 / 4 / 8 kHz"]

        B["Press Button A<br/>Start capture"]

        C["Generate reference tone<br/>200 Hz buzzer"]

        D["Microphone ADC"]

        E["Timed acquisition<br/>Read ADC + timestamp"]

        F["RAM buffers<br/>ADC values + timestamps"]

        G["Temporal analysis<br/>Effective fs<br/>Mean Δt<br/>Jitter<br/>Max lateness"]

        H["Local feedback<br/>OLED + 5x5 matrix + RGB"]

        I["USB serial transmission"]

        A --> B
        B --> C
        C --> D
        D --> E
        E --> F

        F --> G
        G --> H

        F --> I
    end


    %% =========================
    %% HOST COMPUTER
    %% =========================

    subgraph PC["Host PC / Python"]
        direction TB

        J["rita_receiver.py"]

        K["Receive serial stream"]

        L["Temporary CSV<br/>written line by line"]

        M["Pandas validation<br/>and type conversion"]

        N["Parquet capture"]

        O["Partition dataset<br/>by sampling rate"]

        P["captures.csv<br/>capture metadata"]

        Q["analyze_rita.py"]

        R["Compare sampling rates<br/>Timing • Data volume • Signal"]

        J --> K
        K --> L
        L --> M

        M --> N
        N --> O

        M --> P

        O --> Q
        P --> Q

        Q --> R
    end


    I -->|"USB CDC"| J
```
