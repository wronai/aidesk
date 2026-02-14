# AI Desktop Assistant — Konfiguracja providerów AI

Aplikacja używa **LiteLLM** jako zunifikowanej bramki do 100+ providerów LLM.
Zmiana modelu to edycja jednej zmiennej `VISION_MODEL` w `backend/.env` — bez zmian w kodzie.

## Spis treści

- [Szybki start](#szybki-start)
- [Jak działa LiteLLM](#jak-działa-litellm)
- [Providerzy lokalni](#providerzy-lokalni)
  - [Ollama](#ollama)
  - [LM Studio](#lm-studio)
  - [vLLM](#vllm)
  - [llama.cpp server](#llamacpp-server)
  - [Text Generation WebUI](#text-generation-webui)
- [Providerzy zdalni](#providerzy-zdalni)
  - [Google Gemini](#google-gemini)
  - [OpenAI](#openai)
  - [Anthropic Claude](#anthropic-claude)
  - [Groq](#groq)
  - [DeepSeek](#deepseek)
  - [Mistral](#mistral)
- [Tabela porównawcza](#tabela-porównawcza)
- [Zmienne środowiskowe](#zmienne-środowiskowe)
- [Przykładowe konfiguracje](#przykładowe-konfiguracje)
- [Rozwiązywanie problemów](#rozwiązywanie-problemów)

---

## Szybki start

### Opcja 1: Lokalna (zero kosztów, prywatność)

```bash
# 1. Zainstaluj Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# 2. Pobierz model wizyjny
ollama pull llava

# 3. W backend/.env ustaw:
VISION_MODEL=ollama/llava
```

### Opcja 2: Cloud (Google Gemini — najtańszy)

```bash
# 1. Wejdź na https://makersuite.google.com/app/apikey
# 2. Utwórz klucz API (darmowy tier)
# 3. W backend/.env ustaw:
VISION_MODEL=gemini/gemini-2.0-flash
GEMINI_API_KEY=AIza...twój_klucz
```

---

## Jak działa LiteLLM

LiteLLM to biblioteka Python, która standaryzuje wywołania do różnych providerów AI
na format OpenAI-compatible. Zamiast pisać osobny kod dla Gemini, OpenAI, Claude itd.,
używamy jednego wywołania `litellm.acompletion()` — a LiteLLM routuje request
do właściwego API na podstawie prefiksu modelu.

### Format nazwy modelu

```
VISION_MODEL=provider/model-name
```

| Prefiks        | Provider                  | Przykład                                |
|----------------|---------------------------|-----------------------------------------|
| `ollama/`      | Ollama (lokalne)          | `ollama/llava`                          |
| `gemini/`      | Google Gemini             | `gemini/gemini-2.0-flash`               |
| (brak)         | OpenAI                    | `gpt-4o-mini`                           |
| `anthropic/`   | Anthropic Claude          | `anthropic/claude-sonnet-4-20250514`    |
| `groq/`        | Groq                      | `groq/llava-v1.5-7b-4096-preview`       |
| `deepseek/`    | DeepSeek                  | `deepseek/deepseek-chat`                |
| `mistral/`     | Mistral AI                | `mistral/pixtral-12b-2409`              |
| `openai/`      | OpenAI-compatible serwer  | `openai/local-model` + `LITELLM_API_BASE` |

### Zmiana providera

Wystarczy zmienić **jedną linię** w `backend/.env`:

```bash
# Przed:
VISION_MODEL=ollama/llava

# Po:
VISION_MODEL=gemini/gemini-2.0-flash
GEMINI_API_KEY=AIza...
```

Restart backendu (`make stop && make run-backend`) i gotowe.

---

## Providerzy lokalni

Lokalne modele dają **zero kosztów API** i **pełną prywatność** — dane nie opuszczają maszyny.

### Ollama

**Najłatwiejszy sposób na lokalne modele.** Zarządza pobieraniem, kwantyzacją i serwowaniem modeli.

#### Instalacja

```bash
# Linux/macOS
curl -fsSL https://ollama.ai/install.sh | sh

# Lub przez package manager
# macOS: brew install ollama
# Arch:  yay -S ollama
```

#### Modele wizyjne (obsługują obrazy)

```bash
# LLaVA 7B — rekomendowany start (4.7 GB)
ollama pull llava

# LLaVA 13B — lepsza jakość (8 GB)
ollama pull llava:13b

# BakLLaVA — alternatywa LLaVA (4.7 GB)
ollama pull bakllava

# LLaVA-Phi3 — lekki, szybki (2.9 GB)
ollama pull llava-phi3

# Llama 3.2 Vision 11B (6.4 GB)
ollama pull llama3.2-vision
```

#### Konfiguracja .env

```bash
# Podstawowa (Ollama na localhost:11434)
VISION_MODEL=ollama/llava

# Większy model
VISION_MODEL=ollama/llava:13b

# Ollama na innej maszynie
VISION_MODEL=ollama/llava
LITELLM_API_BASE=http://192.168.1.100:11434
```

#### Wymagania sprzętowe

| Model              | RAM     | VRAM (GPU) | Czas odpowiedzi |
|--------------------|---------|------------|------------------|
| `llava-phi3`       | 4 GB    | 3 GB       | ~2s              |
| `llava` (7B)       | 8 GB    | 5 GB       | ~3s              |
| `llava:13b`        | 16 GB   | 8 GB       | ~5s              |
| `llama3.2-vision`  | 16 GB   | 7 GB       | ~4s              |

> **Tip:** Ollama automatycznie używa GPU (NVIDIA CUDA, AMD ROCm, Apple Metal).
> Bez GPU działa na CPU, ale wolniej.

---

### LM Studio

GUI do lokalnych LLM z wbudowanym serwerem OpenAI-compatible.

#### Instalacja

1. Pobierz z https://lmstudio.ai
2. Zainstaluj i uruchom
3. Pobierz model wizyjny (np. `llava-v1.6-mistral-7b`)
4. Kliknij **"Start Server"** (domyślnie port 1234)

#### Konfiguracja .env

```bash
VISION_MODEL=openai/llava-v1.6-mistral-7b
LITELLM_API_BASE=http://localhost:1234/v1
LITELLM_API_KEY=lm-studio
```

> **Uwaga:** Nazwa modelu w `VISION_MODEL` po `openai/` musi odpowiadać nazwie załadowanego
> modelu w LM Studio. Sprawdź w UI LM Studio → Server → "Model name".

---

### vLLM

Wydajny serwer inferencyjny — najszybszy throughput dla GPU.

#### Instalacja i uruchomienie

```bash
pip install vllm

# Uruchom serwer z modelem wizyjnym
vllm serve llava-hf/llava-v1.6-mistral-7b-hf \
  --host 0.0.0.0 \
  --port 8080 \
  --max-model-len 4096
```

#### Konfiguracja .env

```bash
VISION_MODEL=openai/llava-hf/llava-v1.6-mistral-7b-hf
LITELLM_API_BASE=http://localhost:8080/v1
LITELLM_API_KEY=token-abc123
```

---

### llama.cpp server

Lekki serwer C++ — minimalne zużycie zasobów, działa na CPU.

#### Instalacja i uruchomienie

```bash
# Pobierz skompilowany binary lub zbuduj ze źródeł
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp && make -j

# Pobierz model GGUF
wget https://huggingface.co/mys/ggml_llava-v1.5-7b/resolve/main/ggml-model-q4_k.gguf

# Uruchom serwer
./llama-server -m ggml-model-q4_k.gguf --port 8080 -ngl 999
```

#### Konfiguracja .env

```bash
VISION_MODEL=openai/llava-v1.5-7b
LITELLM_API_BASE=http://localhost:8080/v1
LITELLM_API_KEY=not-needed
```

---

### Text Generation WebUI

(oobabooga) — rozbudowany WebUI z wieloma backendami.

#### Konfiguracja .env

```bash
# Domyślny port TG WebUI z OpenAI extension
VISION_MODEL=openai/loaded-model
LITELLM_API_BASE=http://localhost:5000/v1
LITELLM_API_KEY=not-needed
```

---

## Providerzy zdalni

Zdalne API dają dostęp do najpotężniejszych modeli bez wymagań sprzętowych.

### Google Gemini

**Najtańszy cloud provider** — darmowy tier + najniższe ceny per token.

| Parametr          | Wartość |
|-------------------|---------|
| Rejestracja       | https://makersuite.google.com/app/apikey |
| Darmowy tier      | ✅ 15 RPM, 1M tokenów/dzień |
| Cena (Flash)      | $0.10 / 1M input, $0.40 / 1M output |
| Vision support    | ✅ Natywny |
| Latency           | ~500ms |

#### Konfiguracja .env

```bash
VISION_MODEL=gemini/gemini-2.0-flash
GEMINI_API_KEY=AIza...

# Inne modele Gemini:
# VISION_MODEL=gemini/gemini-1.5-pro      ← lepszy, droższy
# VISION_MODEL=gemini/gemini-1.5-flash    ← tańsza wersja
```

---

### OpenAI

| Parametr          | Wartość |
|-------------------|---------|
| Rejestracja       | https://platform.openai.com/api-keys |
| Darmowy tier      | ❌ (wymaga płatności) |
| Cena (4o-mini)    | $0.15 / 1M input, $0.60 / 1M output |
| Cena (4o)         | $2.50 / 1M input, $10.00 / 1M output |
| Vision support    | ✅ Natywny |
| Latency           | ~700ms |

#### Konfiguracja .env

```bash
VISION_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...

# Inne modele:
# VISION_MODEL=gpt-4o          ← najlepsza jakość
# VISION_MODEL=gpt-4-turbo     ← starszy, tańszy
```

---

### Anthropic Claude

| Parametr          | Wartość |
|-------------------|---------|
| Rejestracja       | https://console.anthropic.com/ |
| Darmowy tier      | ❌ (wymaga płatności) |
| Cena (Sonnet)     | $3.00 / 1M input, $15.00 / 1M output |
| Cena (Haiku)      | $0.25 / 1M input, $1.25 / 1M output |
| Vision support    | ✅ Natywny |
| Latency           | ~800ms |

#### Konfiguracja .env

```bash
VISION_MODEL=anthropic/claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-...

# Inne modele:
# VISION_MODEL=anthropic/claude-haiku-4-20250514   ← tańszy, szybszy
# VISION_MODEL=anthropic/claude-opus-4-20250514    ← najlepszy
```

---

### Groq

**Ultra-szybka inferencja** — darmowy tier z limitami.

| Parametr          | Wartość |
|-------------------|---------|
| Rejestracja       | https://console.groq.com/keys |
| Darmowy tier      | ✅ 30 RPM, 14,400 req/dzień |
| Cena              | Darmowy (z limitami) |
| Vision support    | ✅ (LLaVA) |
| Latency           | ~200ms (najszybszy!) |

#### Konfiguracja .env

```bash
VISION_MODEL=groq/llava-v1.5-7b-4096-preview
GROQ_API_KEY=gsk_...
```

---

### DeepSeek

**Chiński provider** — bardzo tanie ceny, dobra jakość.

| Parametr          | Wartość |
|-------------------|---------|
| Rejestracja       | https://platform.deepseek.com/api_keys |
| Darmowy tier      | ✅ (ograniczony) |
| Cena              | $0.14 / 1M input, $0.28 / 1M output |
| Vision support    | ✅ (deepseek-chat) |
| Latency           | ~600ms |

#### Konfiguracja .env

```bash
VISION_MODEL=deepseek/deepseek-chat
DEEPSEEK_API_KEY=sk-...
```

---

### Mistral

| Parametr          | Wartość |
|-------------------|---------|
| Rejestracja       | https://console.mistral.ai/api-keys |
| Darmowy tier      | ✅ (ograniczony) |
| Cena (Pixtral)    | $0.15 / 1M input, $0.60 / 1M output |
| Vision support    | ✅ (Pixtral) |
| Latency           | ~500ms |

#### Konfiguracja .env

```bash
VISION_MODEL=mistral/pixtral-12b-2409
MISTRAL_API_KEY=...
```

---

## Tabela porównawcza

| Provider           | Model                    | Vision | Koszt/1K req* | Latency | Prywatność | GPU wymagane |
|--------------------|--------------------------|--------|---------------|---------|------------|--------------|
| **Ollama**         | `ollama/llava`           | ✅     | $0            | ~3s     | ✅ Pełna   | Opcjonalne   |
| **LM Studio**      | `openai/llava-v1.6`      | ✅     | $0            | ~3s     | ✅ Pełna   | Opcjonalne   |
| **Groq**           | `groq/llava-v1.5-7b`    | ✅     | $0 (free)     | ~200ms  | ❌         | ❌           |
| **Gemini Flash**   | `gemini/gemini-2.0-flash`| ✅     | ~$0.15        | ~500ms  | ❌         | ❌           |
| **DeepSeek**       | `deepseek/deepseek-chat` | ✅     | ~$0.20        | ~600ms  | ❌         | ❌           |
| **GPT-4o-mini**    | `gpt-4o-mini`            | ✅     | ~$0.35        | ~700ms  | ❌         | ❌           |
| **Mistral Pixtral**| `mistral/pixtral-12b`    | ✅     | ~$0.35        | ~500ms  | ❌         | ❌           |
| **Claude Haiku**   | `anthropic/claude-haiku-4`| ✅    | ~$1.50        | ~600ms  | ❌         | ❌           |
| **GPT-4o**         | `gpt-4o`                 | ✅     | ~$6.00        | ~700ms  | ❌         | ❌           |
| **Claude Sonnet**  | `anthropic/claude-sonnet-4`| ✅   | ~$10.00       | ~800ms  | ❌         | ❌           |

*Szacowany koszt za 1000 requestów z obrazem 1280×720, ~100 tokenów output.

### Rekomendacje

- **Najlepszy start:** `ollama/llava` — zero kosztów, pełna prywatność
- **Najlepszy cloud:** `gemini/gemini-2.0-flash` — tani, szybki, darmowy tier
- **Najszybszy:** `groq/llava-v1.5-7b-4096-preview` — 200ms, darmowy tier
- **Najlepsza jakość:** `anthropic/claude-sonnet-4-20250514` — najdroższy, ale najlepszy

---

## Zmienne środowiskowe

Wszystkie zmienne konfiguruje się w `backend/.env`.

### Vision (wymagane)

| Zmienna              | Opis                                           | Domyślna          |
|----------------------|-------------------------------------------------|--------------------|
| `VISION_MODEL`       | Model LiteLLM (provider/model-name)             | `ollama/llava`     |
| `LITELLM_API_BASE`   | Custom API URL dla lokalnych serwerów           | *(puste)*          |
| `LITELLM_API_KEY`    | Override klucza API                             | *(puste)*          |
| `VISION_MAX_TOKENS`  | Maks. tokenów w odpowiedzi                      | `400`              |
| `VISION_TEMPERATURE` | Temperatura samplowania (0.0–1.0)               | `0.3`              |
| `VISION_IMAGE_DETAIL`| Szczegółowość obrazu: `low`, `high`, `auto`    | `low`              |

### API Keys (wymagane dla zdalnych providerów)

| Zmienna            | Provider      | Gdzie uzyskać                                |
|--------------------|---------------|----------------------------------------------|
| `GEMINI_API_KEY`   | Google Gemini | https://makersuite.google.com/app/apikey     |
| `OPENAI_API_KEY`   | OpenAI        | https://platform.openai.com/api-keys         |
| `ANTHROPIC_API_KEY`| Anthropic     | https://console.anthropic.com/               |
| `GROQ_API_KEY`     | Groq          | https://console.groq.com/keys                |
| `DEEPSEEK_API_KEY` | DeepSeek      | https://platform.deepseek.com/api_keys       |
| `MISTRAL_API_KEY`  | Mistral       | https://console.mistral.ai/api-keys          |
| `DEEPGRAM_API_KEY` | Deepgram STT  | https://deepgram.com                         |

> **Uwaga:** Klucze API dla lokalnych providerów (Ollama, LM Studio, vLLM) nie są wymagane.

---

## Przykładowe konfiguracje

### Konfiguracja 1: Pełna prywatność (lokalna)

```bash
VISION_MODEL=ollama/llava:13b
ENABLE_STT=false
```

### Konfiguracja 2: Hybrydowa (lokalna vision + cloud STT)

```bash
VISION_MODEL=ollama/llava
DEEPGRAM_API_KEY=twój_klucz
ENABLE_STT=true
```

### Konfiguracja 3: Najniższy koszt cloud

```bash
VISION_MODEL=gemini/gemini-2.0-flash
GEMINI_API_KEY=AIza...
```

### Konfiguracja 4: Najwyższa jakość

```bash
VISION_MODEL=anthropic/claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-...
```

### Konfiguracja 5: LM Studio na zdalnej maszynie

```bash
VISION_MODEL=openai/llava-v1.6-mistral-7b
LITELLM_API_BASE=http://192.168.1.100:1234/v1
LITELLM_API_KEY=lm-studio
```

---

## Rozwiązywanie problemów

### Ollama nie odpowiada

```bash
# Sprawdź czy Ollama jest uruchomiona
curl http://localhost:11434/api/tags

# Uruchom jeśli nie działa
ollama serve

# Sprawdź zainstalowane modele
ollama list
```

### LiteLLM nie rozpoznaje modelu

```bash
# Sprawdź listę obsługiwanych modeli
python -c "import litellm; print(litellm.model_list)"

# Włącz debug mode w .env
DEBUG=true
```

### Błąd "API key not found"

LiteLLM szuka kluczy w zmiennych środowiskowych automatycznie:
- `GEMINI_API_KEY` dla `gemini/`
- `OPENAI_API_KEY` dla modeli OpenAI
- `ANTHROPIC_API_KEY` dla `anthropic/`
- `GROQ_API_KEY` dla `groq/`

Upewnij się, że odpowiedni klucz jest ustawiony w `backend/.env`.

### Model nie obsługuje vision

Nie wszystkie modele LLM obsługują obrazy. Jeśli widzisz błąd, sprawdź czy model
ma wsparcie vision w dokumentacji providera. Bezpieczne wybory:

- Ollama: `llava`, `llava:13b`, `bakllava`, `llava-phi3`, `llama3.2-vision`
- Gemini: `gemini-2.0-flash`, `gemini-1.5-pro`, `gemini-1.5-flash`
- OpenAI: `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`
- Anthropic: Wszystkie modele Claude 3+
- Groq: `llava-v1.5-7b-4096-preview`
- Mistral: `pixtral-12b-2409`, `pixtral-large-2411`

### Wydajność lokalna jest słaba

1. **Użyj mniejszego modelu:** `ollama/llava-phi3` zamiast `ollama/llava:13b`
2. **Zmniejsz rozdzielczość:** `SCREEN_WIDTH=960`, `SCREEN_HEIGHT=540`
3. **Zwiększ interwał:** `MIN_CAPTURE_INTERVAL=3.0`
4. **Użyj GPU:** Ollama automatycznie wykrywa CUDA/ROCm/Metal

---

*Dokumentacja aktualizowana: 2026-02-14*
*LiteLLM docs: https://docs.litellm.ai/docs/providers*
