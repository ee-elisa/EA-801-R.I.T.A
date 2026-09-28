# RITA - Projeto 1 / EA801
# BitDogLab V7
#
# Versao sem FFT: foco em temporizacao, volume de dados e sinal.
#
# CONTROLES
#   Joystick Y ........ muda fs: 1 / 2 / 4 / 8 kHz
#   Botao A ........... inicia captura de 2 s
#   Botao B ........... troca pagina do OLED
#   Clique joystick ... alterna o mapa da matriz:
#                       TIMING <-> DEADLINE
#
# BUZZER
#   Tom FIXO em 200 Hz.
#
# OLED
#   READY ........ configuracao
#   PAGINA 1 ..... resumo
#   PAGINA 2 ..... timing
#   PAGINA 3 ..... volume de dados
#   PAGINA 4 ..... "osciloscopio" de 25 ms
#
# MATRIZ 5x5
#   Cada LED representa 1/25 da captura = 80 ms.
#   TIMING: cor = erro medio de Delta-t no trecho.
#   DEADLINE: cor = pior atraso em relacao ao instante ideal.
#
# IMPORTANTE
#   OLED, matriz, RGB e serial NAO sao atualizados dentro do
#   loop critico de aquisicao.

from machine import Pin, ADC, PWM, SoftI2C
from ssd1306 import SSD1306_I2C
import neopixel
import time
import gc
import struct
import sys
import math

# ============================================================
# PINOS
# ============================================================

MIC_PIN = 28
BUZZER_PIN = 21

BUTTON_A_PIN = 5
BUTTON_B_PIN = 6

JOYSTICK_X_PIN = 27
JOYSTICK_Y_PIN = 26
JOYSTICK_BUTTON_PIN = 22

OLED_SDA_PIN = 2
OLED_SCL_PIN = 3

LED_MATRIX_PIN = 7
NUM_LEDS = 25

RGB_R_PIN = 13
RGB_G_PIN = 11
RGB_B_PIN = 12

# ============================================================
# EXPERIMENTO
# ============================================================

SAMPLING_RATES = [1000, 2000, 4000, 8000]
selected_fs_index = 3

DURATION_S = 2
TONE_HZ = 200
BUZZER_DUTY = 10000
TONE_SETTLE_MS = 100

JOY_LOW = 15000
JOY_HIGH = 50000

# Pagina SIGNAL mostra sempre a mesma janela temporal.
# 25 ms = 5 ciclos do tom de 200 Hz.
OSC_WINDOW_MS = 25
OSC_START_FRACTION = 0.25

# ============================================================
# CORES
# ============================================================

BLACK = (0, 0, 0)

READY_BLUE = (0, 0, 32)
SEND_YELLOW = (34, 26, 0)

DIM_GREEN = (0, 10, 0)
GREEN = (0, 34, 0)
YELLOW = (34, 28, 0)
ORANGE = (40, 12, 0)
RED = (44, 0, 0)

LED_MATRIX = [
    [24, 23, 22, 21, 20],
    [15, 16, 17, 18, 19],
    [14, 13, 12, 11, 10],
    [5, 6, 7, 8, 9],
    [4, 3, 2, 1, 0],
]

# Ordem visual: esquerda -> direita, cima -> baixo.
DISPLAY_ORDER = [
    LED_MATRIX[row][col]
    for row in range(5)
    for col in range(5)
]

# ============================================================
# PERIFERICOS
# ============================================================

mic = ADC(Pin(MIC_PIN))

joystick_x = ADC(Pin(JOYSTICK_X_PIN))
joystick_y = ADC(Pin(JOYSTICK_Y_PIN))

button_a = Pin(BUTTON_A_PIN, Pin.IN, Pin.PULL_UP)
button_b = Pin(BUTTON_B_PIN, Pin.IN, Pin.PULL_UP)

joystick_button = Pin(
    JOYSTICK_BUTTON_PIN,
    Pin.IN,
    Pin.PULL_UP
)

buzzer = PWM(Pin(BUZZER_PIN))
buzzer.freq(TONE_HZ)
buzzer.duty_u16(0)

i2c = SoftI2C(
    scl=Pin(OLED_SCL_PIN),
    sda=Pin(OLED_SDA_PIN)
)

oled = SSD1306_I2C(
    128,
    64,
    i2c
)

matrix = neopixel.NeoPixel(
    Pin(LED_MATRIX_PIN),
    NUM_LEDS
)

rgb_r = PWM(Pin(RGB_R_PIN))
rgb_g = PWM(Pin(RGB_G_PIN))
rgb_b = PWM(Pin(RGB_B_PIN))

for channel in (rgb_r, rgb_g, rgb_b):
    channel.freq(1000)
    channel.duty_u16(0)

# ============================================================
# ESTADO
# ============================================================

last_values = None
last_timestamps = None
last_fs = None
last_n_samples = None
last_metrics = None
last_maps = None

# 0=summary, 1=timing, 2=data, 3=signal
result_page = 0

# 0=timing map, 1=deadline map
matrix_mode = 0

# ============================================================
# RGB = ESTADO
# ============================================================

def rgb_set(r, g, b):
    scale = 16000

    rgb_r.duty_u16(
        int(max(0, min(255, r)) * scale / 255)
    )

    rgb_g.duty_u16(
        int(max(0, min(255, g)) * scale / 255)
    )

    rgb_b.duty_u16(
        int(max(0, min(255, b)) * scale / 255)
    )


def rgb_ready():
    rgb_set(0, 0, 255)


def rgb_recording():
    rgb_set(255, 0, 0)


def rgb_sending():
    rgb_set(255, 170, 0)


def rgb_done():
    rgb_set(0, 255, 0)


# ============================================================
# MATRIZ - BASICO
# ============================================================

def matrix_clear(write=True):
    for i in range(NUM_LEDS):
        matrix[i] = BLACK

    if write:
        matrix.write()


def matrix_linear(level, color):
    level = max(
        0,
        min(NUM_LEDS, int(level))
    )

    for visual_index, physical_index in enumerate(DISPLAY_ORDER):
        matrix[physical_index] = (
            color
            if visual_index < level
            else BLACK
        )

    matrix.write()


def matrix_expected_volume(fs):
    n_samples = fs * DURATION_S
    max_samples = max(SAMPLING_RATES) * DURATION_S

    level = int(round(
        NUM_LEDS * n_samples / max_samples
    ))

    level = max(
        1,
        min(NUM_LEDS, level)
    )

    matrix_linear(
        level,
        READY_BLUE
    )


def matrix_transmission_progress(level):
    matrix_linear(
        level,
        SEND_YELLOW
    )


# ============================================================
# MATRIZ - MAPAS TEMPORAIS
# ============================================================

def timing_error_color(ratio):
    """
    ratio = erro medio absoluto de dt / periodo ideal.

    Verde escuro : < 1%
    Verde        : < 2.5%
    Amarelo      : < 5%
    Laranja      : < 10%
    Vermelho     : >= 10%
    """
    if ratio < 0.01:
        return DIM_GREEN

    if ratio < 0.025:
        return GREEN

    if ratio < 0.05:
        return YELLOW

    if ratio < 0.10:
        return ORANGE

    return RED


def deadline_color(ratio):
    """
    ratio = pior atraso em relacao ao deadline / periodo ideal.

    Verde escuro : < 5%
    Verde        : < 10%
    Amarelo      : < 25%
    Laranja      : < 50%
    Vermelho     : >= 50%
    """
    if ratio < 0.05:
        return DIM_GREEN

    if ratio < 0.10:
        return GREEN

    if ratio < 0.25:
        return YELLOW

    if ratio < 0.50:
        return ORANGE

    return RED


def render_timing_map():
    if last_maps is None:
        return

    matrix_clear(write=False)

    ratios = last_maps["timing_ratio"]

    for visual_index in range(NUM_LEDS):
        matrix[
            DISPLAY_ORDER[visual_index]
        ] = timing_error_color(
            ratios[visual_index]
        )

    matrix.write()


def render_deadline_map():
    if last_maps is None:
        return

    matrix_clear(write=False)

    ratios = last_maps["deadline_ratio"]

    for visual_index in range(NUM_LEDS):
        matrix[
            DISPLAY_ORDER[visual_index]
        ] = deadline_color(
            ratios[visual_index]
        )

    matrix.write()


def render_matrix_mode():
    if last_maps is None:
        matrix_expected_volume(
            selected_fs()
        )
        return

    if matrix_mode == 0:
        render_timing_map()
    else:
        render_deadline_map()


def matrix_mode_name():
    if matrix_mode == 0:
        return "TIMING"
    return "DEADLINE"


# ============================================================
# OLED - HELPERS
# ============================================================

def show_lines(lines):
    oled.fill(0)

    for i, line in enumerate(lines[:8]):
        oled.text(
            str(line),
            0,
            i * 8
        )

    oled.show()


def selected_fs():
    return SAMPLING_RATES[
        selected_fs_index
    ]


def wait_release(pin):
    while pin.value() == 0:
        time.sleep_ms(10)


# ============================================================
# OLED - READY / PAGINAS
# ============================================================

def show_config():
    fs = selected_fs()
    n_samples = fs * DURATION_S
    period_us = 1_000_000 // fs

    show_lines([
        "RITA - EA801",
        "CONFIG",
        "fs: {} Hz".format(fs),
        "T: {} s".format(DURATION_S),
        "N: {}".format(n_samples),
        "dt: {} us".format(period_us),
        "tone: {} Hz".format(TONE_HZ),
        "A: capturar",
    ])

    matrix_expected_volume(fs)
    rgb_ready()


def show_help():
    show_lines([
        "RITA CONTROLES",
        "Joy Y: muda fs",
        "A: captura",
        "B: pagina OLED",
        "Joy click:",
        "mapa 5x5",
        "tone fixo 200Hz",
        "",
    ])


def show_capture(
    fs,
    n_samples
):
    show_lines([
        "RITA - EA801",
        "CAPTURANDO...",
        "",
        "fs: {} Hz".format(fs),
        "tone: {} Hz".format(TONE_HZ),
        "N: {}".format(n_samples),
        "T: {} s".format(DURATION_S),
        "sem UI no loop",
    ])


def show_sending(
    fs,
    n_samples
):
    show_lines([
        "RITA - EA801",
        "TRANSMITINDO",
        "",
        "fs: {} Hz".format(fs),
        "N: {}".format(n_samples),
        "",
        "USB CDC -> PC",
        "",
    ])


def show_result_summary():
    m = last_metrics

    show_lines([
        "RITA RESULTADO",
        "fs: {} Hz".format(last_fs),
        "N: {}".format(last_n_samples),
        "eff:{:.2f} Hz".format(
            m["effective_fs"]
        ),
        "err:{:.3f}%".format(
            m["error_pct"]
        ),
        "jit:{:.2f} us".format(
            m["jitter_std_us"]
        ),
        "Mx:{}".format(
            matrix_mode_name()
        ),
        "B: timing",
    ])


def show_timing_page():
    m = last_metrics

    show_lines([
        "TIMING",
        "ideal:{:.2f}us".format(
            1_000_000.0 / last_fs
        ),
        "mean:{:.2f}us".format(
            m["mean_dt_us"]
        ),
        "jit:{:.2f}us".format(
            m["jitter_std_us"]
        ),
        "max late:{:.1f}us".format(
            m["max_lateness_us"]
        ),
        "eff:{:.2f}Hz".format(
            m["effective_fs"]
        ),
        "Mx:{}".format(
            matrix_mode_name()
        ),
        "B: dados",
    ])


def show_data_page():
    adc_bytes = last_n_samples * 2
    all_bytes = last_n_samples * 6

    relative = (
        last_fs
        / min(SAMPLING_RATES)
    )

    show_lines([
        "DATA",
        "samples: {}".format(
            last_n_samples
        ),
        "ADC:{:.1f} KiB".format(
            adc_bytes / 1024.0
        ),
        "+time:{:.1f}KiB".format(
            all_bytes / 1024.0
        ),
        "vs 1k: {:.1f}x".format(
            relative
        ),
        "T: {} s".format(
            DURATION_S
        ),
        "tone: {} Hz".format(
            TONE_HZ
        ),
        "B: SIGNAL",
    ])


# ============================================================
# OLED - OSCILOSCOPIO
# ============================================================

def draw_line(x0, y0, x1, y1):
    """
    Bresenham simples para nao depender de oled.line().
    """
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1

    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1

    err = dx + dy

    while True:
        if (
            0 <= x0 < 128
            and 0 <= y0 < 64
        ):
            oled.pixel(x0, y0, 1)

        if x0 == x1 and y0 == y1:
            break

        e2 = 2 * err

        if e2 >= dy:
            err += dy
            x0 += sx

        if e2 <= dx:
            err += dx
            y0 += sy


def show_signal_page():
    """
    Mini-osciloscopio.

    Mostra SEMPRE 25 ms de sinal.
    Como o buzzer e 200 Hz:
        periodo = 5 ms
        25 ms = 5 ciclos.

    Numero de amostras na janela:
        1 kHz -> 25
        2 kHz -> 50
        4 kHz -> 100
        8 kHz -> 200

    Isso deixa a diferenca de densidade de amostragem visivel
    diretamente no display.
    """

    if last_values is None:
        show_config()
        return

    fs = last_fs

    window_samples = int(
        fs
        * OSC_WINDOW_MS
        / 1000
    )

    window_samples = max(
        2,
        min(
            last_n_samples,
            window_samples
        )
    )

    start = int(
        last_n_samples
        * OSC_START_FRACTION
    )

    if (
        start + window_samples
        > last_n_samples
    ):
        start = (
            last_n_samples
            - window_samples
        )

    # Estatisticas da janela.
    total = 0.0
    v_min = 65535
    v_max = 0

    for j in range(window_samples):
        value = sample_at(
            last_values,
            start + j
        )

        total += value

        if value < v_min:
            v_min = value

        if value > v_max:
            v_max = value

    mean = total / window_samples

    max_dev = max(
        abs(v_max - mean),
        abs(v_min - mean)
    )

    if max_dev < 1.0:
        max_dev = 1.0

    p2p = v_max - v_min

    # Layout.
    oled.fill(0)

    oled.text(
        "SIGNAL {}ms".format(
            OSC_WINDOW_MS
        ),
        0,
        0
    )

    oled.text(
        "fs:{} Nw:{}".format(
            fs,
            window_samples
        ),
        0,
        8
    )

    plot_x0 = 0
    plot_x1 = 127

    plot_y0 = 18
    plot_y1 = 51

    plot_h = (
        plot_y1 - plot_y0
    )

    center_y = (
        plot_y0 + plot_y1
    ) // 2

    # Linha central pontilhada.
    for x in range(
        plot_x0,
        plot_x1 + 1,
        4
    ):
        oled.pixel(
            x,
            center_y,
            1
        )

    previous_x = None
    previous_y = None

    # Marcadores maiores so quando ha poucas amostras.
    big_markers = (
        window_samples <= 60
    )

    for j in range(window_samples):
        value = sample_at(
            last_values,
            start + j
        )

        x = int(round(
            plot_x0
            + j
            * (plot_x1 - plot_x0)
            / (window_samples - 1)
        ))

        normalized = (
            (value - mean)
            / max_dev
        )

        normalized = max(
            -1.0,
            min(1.0, normalized)
        )

        y = int(round(
            center_y
            - normalized
            * (plot_h / 2.0 - 1)
        ))

        y = max(
            plot_y0,
            min(plot_y1, y)
        )

        if previous_x is not None:
            draw_line(
                previous_x,
                previous_y,
                x,
                y
            )

        # O ponto real da amostra.
        oled.pixel(
            x,
            y,
            1
        )

        # Em 1/2 kHz deixa os pontos bem evidentes.
        if big_markers:
            if x + 1 <= plot_x1:
                oled.pixel(
                    x + 1,
                    y,
                    1
                )

            if y + 1 <= plot_y1:
                oled.pixel(
                    x,
                    y + 1,
                    1
                )

        previous_x = x
        previous_y = y

    oled.text(
        "200Hz p2p:{}".format(
            p2p
        ),
        0,
        56
    )

    oled.show()


def render_result_page():
    if last_metrics is None:
        show_config()
        return

    page = result_page % 4

    if page == 0:
        show_result_summary()

    elif page == 1:
        show_timing_page()

    elif page == 2:
        show_data_page()

    else:
        show_signal_page()


# ============================================================
# JOYSTICK - FS
# ============================================================

def joystick_neutral_y():
    y = joystick_y.read_u16()

    return (
        JOY_LOW
        <= y
        <= JOY_HIGH
    )


def wait_y_neutral():
    while not joystick_neutral_y():
        time.sleep_ms(20)


def update_fs_from_joystick():
    global selected_fs_index
    global result_page

    y = joystick_y.read_u16()

    if y > JOY_HIGH:
        selected_fs_index = (
            selected_fs_index + 1
        ) % len(SAMPLING_RATES)

        result_page = 0

        show_config()
        wait_y_neutral()
        return True

    if y < JOY_LOW:
        selected_fs_index = (
            selected_fs_index - 1
        ) % len(SAMPLING_RATES)

        result_page = 0

        show_config()
        wait_y_neutral()
        return True

    return False


# ============================================================
# BUFFER HELPERS
# ============================================================

def sample_at(
    values,
    i
):
    return struct.unpack_from(
        "<H",
        values,
        i * 2
    )[0]


def timestamp_at(
    timestamps,
    i
):
    return struct.unpack_from(
        "<I",
        timestamps,
        i * 4
    )[0]


# ============================================================
# AQUISICAO
# ============================================================

def capture(fs):
    n_samples = (
        fs * DURATION_S
    )

    period_us = (
        1_000_000 // fs
    )

    values = bytearray(
        n_samples * 2
    )

    timestamps = bytearray(
        n_samples * 4
    )

    gc.collect()

    show_capture(
        fs,
        n_samples
    )

    rgb_recording()

    print("# PREPARING_CAPTURE")

    print(
        "# fs_hz={} n={} duration_s={} tone_hz={}".format(
            fs,
            n_samples,
            DURATION_S,
            TONE_HZ
        )
    )

    buzzer.freq(TONE_HZ)
    buzzer.duty_u16(
        BUZZER_DUTY
    )

    time.sleep_ms(
        TONE_SETTLE_MS
    )

    start_us = time.ticks_us()
    target_us = start_us

    gc.disable()

    try:
        for i in range(n_samples):

            while time.ticks_diff(
                target_us,
                time.ticks_us()
            ) > 0:
                pass

            now_us = time.ticks_us()

            elapsed_us = time.ticks_diff(
                now_us,
                start_us
            )

            value = mic.read_u16()

            struct.pack_into(
                "<H",
                values,
                i * 2,
                value
            )

            struct.pack_into(
                "<I",
                timestamps,
                i * 4,
                elapsed_us
            )

            target_us = time.ticks_add(
                target_us,
                period_us
            )

    finally:
        buzzer.duty_u16(0)
        gc.enable()

    return (
        values,
        timestamps,
        n_samples
    )


# ============================================================
# METRICAS TEMPORAIS + MAPAS
# ============================================================

def compute_temporal_analysis(
    timestamps,
    fs,
    n_samples
):
    period_us = (
        1_000_000.0 / fs
    )

    t0 = timestamp_at(
        timestamps,
        0
    )

    t_last = timestamp_at(
        timestamps,
        n_samples - 1
    )

    duration_us = (
        t_last - t0
    )

    if duration_us <= 0:
        effective_fs = 0.0
    else:
        effective_fs = (
            (n_samples - 1)
            * 1_000_000.0
            / duration_us
        )

    error_pct = (
        abs(
            effective_fs - fs
        )
        / fs
        * 100.0
    )

    sum_dt = 0.0
    sum_sq = 0.0

    max_lateness = 0.0

    timing_error_sum = [
        0.0
    ] * NUM_LEDS

    timing_error_count = [
        0
    ] * NUM_LEDS

    deadline_max = [
        0.0
    ] * NUM_LEDS

    previous = t0

    for i in range(
        1,
        n_samples
    ):
        current = timestamp_at(
            timestamps,
            i
        )

        dt = (
            current - previous
        )

        previous = current

        sum_dt += dt
        sum_sq += (
            dt * dt
        )

        # Cada segmento corresponde a 1/25 da captura.
        segment = int(
            i
            * NUM_LEDS
            / n_samples
        )

        if segment >= NUM_LEDS:
            segment = (
                NUM_LEDS - 1
            )

        spacing_error = abs(
            dt - period_us
        )

        timing_error_sum[
            segment
        ] += spacing_error

        timing_error_count[
            segment
        ] += 1

        ideal_elapsed = (
            i * period_us
        )

        actual_elapsed = (
            current - t0
        )

        lateness = (
            actual_elapsed
            - ideal_elapsed
        )

        # Atraso positivo e o que interessa no deadline map.
        if lateness < 0:
            lateness = 0.0

        if (
            lateness
            > deadline_max[segment]
        ):
            deadline_max[
                segment
            ] = lateness

        if lateness > max_lateness:
            max_lateness = lateness

    count = n_samples - 1

    mean_dt = (
        sum_dt / count
    )

    variance = (
        sum_sq / count
        - mean_dt * mean_dt
    )

    if variance < 0:
        variance = 0.0

    jitter_std = math.sqrt(
        variance
    )

    timing_ratio = []
    deadline_ratio = []

    for segment in range(
        NUM_LEDS
    ):
        if (
            timing_error_count[
                segment
            ] > 0
        ):
            mean_abs_error = (
                timing_error_sum[
                    segment
                ]
                / timing_error_count[
                    segment
                ]
            )
        else:
            mean_abs_error = 0.0

        timing_ratio.append(
            mean_abs_error
            / period_us
        )

        deadline_ratio.append(
            deadline_max[
                segment
            ]
            / period_us
        )

    jitter_ratio = (
        jitter_std
        / period_us
    )

    if (
        error_pct < 1.0
        and jitter_ratio < 0.10
    ):
        quality = "GOOD"

    elif (
        error_pct < 5.0
        and jitter_ratio < 0.25
    ):
        quality = "WARN"

    else:
        quality = "BAD"

    metrics = {
        "effective_fs": effective_fs,
        "error_pct": error_pct,
        "mean_dt_us": mean_dt,
        "jitter_std_us": jitter_std,
        "max_lateness_us": max_lateness,
        "quality": quality,
    }

    maps = {
        "timing_ratio": timing_ratio,
        "deadline_ratio": deadline_ratio,
    }

    return metrics, maps


# ============================================================
# TRANSMISSAO
# ============================================================

def send_capture(
    values,
    timestamps,
    fs,
    n_samples
):
    show_sending(
        fs,
        n_samples
    )

    rgb_sending()

    matrix_transmission_progress(
        0
    )

    print("# BEGIN_CAPTURE")
    print(
        "sample_id,timestamp_us,value,fs_hz"
    )

    last_level = -1

    for i in range(
        n_samples
    ):
        value = sample_at(
            values,
            i
        )

        timestamp_us = timestamp_at(
            timestamps,
            i
        )

        sys.stdout.write(
            "{},{},{},{}\n".format(
                i,
                timestamp_us,
                value,
                fs
            )
        )

        level = (
            (i + 1)
            * NUM_LEDS
            // n_samples
        )

        if level != last_level:
            matrix_transmission_progress(
                level
            )

            last_level = level

    print("# END_CAPTURE")


# ============================================================
# EXPERIMENTO COMPLETO
# ============================================================

def run_experiment():
    global last_values
    global last_timestamps
    global last_fs
    global last_n_samples
    global last_metrics
    global last_maps
    global result_page
    global matrix_mode

    fs = selected_fs()

    print(
        "# CAPTURE_STARTED fs={} tone={}".format(
            fs,
            TONE_HZ
        )
    )

    (
        values,
        timestamps,
        n_samples
    ) = capture(fs)

    (
        metrics,
        maps
    ) = compute_temporal_analysis(
        timestamps,
        fs,
        n_samples
    )

    print("# CAPTURE_FINISHED")

    print(
        "# METRICS effective={:.3f} error_pct={:.4f} "
        "mean_dt_us={:.3f} jitter_std_us={:.3f} "
        "max_lateness_us={:.3f} quality={}".format(
            metrics["effective_fs"],
            metrics["error_pct"],
            metrics["mean_dt_us"],
            metrics["jitter_std_us"],
            metrics["max_lateness_us"],
            metrics["quality"],
        )
    )

    print("# SENDING_TO_PC")

    send_capture(
        values,
        timestamps,
        fs,
        n_samples
    )

    last_values = values
    last_timestamps = timestamps
    last_fs = fs
    last_n_samples = n_samples
    last_metrics = metrics
    last_maps = maps

    # Resultado inicial da matriz = mapa de timing.
    matrix_mode = 0
    render_matrix_mode()

    rgb_done()

    # Depois da captura, abre DIRETO o osciloscopio.
    result_page = 3
    render_result_page()

    print("# READY")


# ============================================================
# MAIN
# ============================================================

def main():
    global result_page
    global matrix_mode

    print()
    print("# ========================================")
    print("# RITA - Projeto 1 - BitDogLab V7")
    print("# ========================================")
    print("# tone fixo = 200 Hz")
    print("# joystick Y = sampling rate")
    print("# A = captura")
    print("# B = pagina OLED")
    print("# joystick click = mapa TIMING/DEADLINE")
    print("# READY")

    show_config()

    while True:

        if update_fs_from_joystick():
            time.sleep_ms(20)
            continue

        # A = captura
        if button_a.value() == 0:
            time.sleep_ms(30)

            if button_a.value() == 0:
                wait_release(
                    button_a
                )

                run_experiment()
                continue

        # B = pagina OLED
        if button_b.value() == 0:
            time.sleep_ms(30)

            if button_b.value() == 0:
                wait_release(
                    button_b
                )

                if last_metrics is None:
                    show_help()
                    time.sleep_ms(1600)
                    show_config()

                else:
                    result_page = (
                        result_page + 1
                    ) % 4

                    render_result_page()

                continue

        # Clique joystick = TIMING <-> DEADLINE
        if joystick_button.value() == 0:
            time.sleep_ms(30)

            if joystick_button.value() == 0:
                wait_release(
                    joystick_button
                )

                if last_maps is not None:
                    matrix_mode = (
                        matrix_mode + 1
                    ) % 2

                    render_matrix_mode()

                    # Atualiza texto da pagina atual se houver
                    # indicacao do modo.
                    render_result_page()

                else:
                    show_help()
                    time.sleep_ms(1200)
                    show_config()

                continue

        time.sleep_ms(10)


try:
    main()

finally:
    buzzer.duty_u16(0)
    matrix_clear()
    rgb_set(0, 0, 0)
