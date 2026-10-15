---
name: win-ui-find
description: >-
  Поиск и действие по UI-элементу на Windows без VLM: UI Automation (имя/роль/окно),
  OCR fallback в bounds окна, Invoke или клик по координатам. Use when найти кнопку
  на экране, кликнуть без vision, UIA, accessibility tree, OCR вместо VLM,
  computer use Windows, screen element find, win-ui-find.
---

# Windows UI find (без VLM)

Лестница: **UIA → OCR окна → Invoke/клик**. Скриншот в LLM / grounding VLM не использовать.

Скрипты: [scripts/](scripts/) рядом с этим `SKILL.md`.

## Установка (один раз)

```powershell
cd <path-to>/skills/win-ui-find/scripts
# предпочтительно venv (uv/PEP 668):
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# OCR fallback (хотя бы один):
.\.venv\Scripts\python.exe -m pip install winocr
# или: .\.venv\Scripts\python.exe -m pip install rapidocr-onnxruntime
.\.venv\Scripts\python.exe probe.py
```

Дальше везде `PYTHON` = `skills/win-ui-find/scripts/.venv/Scripts/python.exe` (или системный `python`, если пакеты уже стоят).

`probe.py` должен показать `"uia": {"ok": true}` и список `top_windows`.

## Когда применять

- Нужно найти кнопку/поле/пункт меню на рабочем столе Windows
- Агент тянется к скриншоту + VLM - остановить и идти через этот skill
- Браузер: сначала DOM/a11y MCP (`ref`); этот skill - для нативных окон (1С, Explorer, IDE, диалоги)

## Жёсткие правила

1. Сначала `probe.py` / `--list`, потом точечный `--name`.
2. Scope всегда в **окно** (`--window`), не весь экран.
3. Действие по умолчанию - **dry-run** (`act.py` без `--yes`).
4. Предпочитать **Invoke** координатному клику **если** UI реально изменился.
   На кнопках 1С Taxi Invoke часто `performed: true` вхолостую - тогда
   physical click (повтор `--action click` по hit / крестик модалки).
5. OCR только с `--ocr`, только если UIA пуст.
6. VLM / "look at screenshot and click" - запрещены этим skill.
7. Не кликать в чужое окно: сверять `window` в JSON-ответе.
8. **Точность координат (2026-10-04):**
   - OCR-захват окна - через `PrintWindow` (`coords.grab_window_image_printwindow`),
     не сырой `ImageGrab` поверх Cursor (иначе OCR/клики по чужому UI).
   - Поле формы: UIA Edit/CheckBox по `Name` (`field_click.py`); OCR+sweep -
     fallback. Не `label.right+N`.
   - После клика - `coords.py point --x --y` / read-clip; не писать вслепую.
   - DPI: `ensure_dpi_aware()` в coords (на 100% scale часто 96, но включать всегда).
9. **Не одноразовые скрипты в Temp (HARD GATE)** - запрещён `Write`/
   `Set-Content` `.py`/`.ps1` в `%TEMP%` даже как «тонкая обёртка» над CLI.
   Расширять `field_click` / `act` / `find` или сценарии в
   `1c-desktop-session/scripts`. Кириллица:  
   `1c-desktop-session/scripts/run_argv.py --script field_click --args-b64 …`.
   Правило `1c-skill-scripts-only.mdc`.
10. **`WalkControl`:** в части сборок `uiautomation` отдаёт `(Control, depth)` -
    брать `item[0]`, иначе `'tuple' object has no attribute 'Name'`.
11. Двойной клик по spreadsheet 1С: `ctypes mouse_event` LEFTDOWN/UP x2; модуль
    `uiautomation` может **не** иметь `DoubleClick`.

Сценарии Taxi 1С (фокус, OCR окна, модалки, поиск списка, typeahead,
OS-auth карточки, run_argv для кириллицы) - skill **`1c-desktop-session`**,
не копипаста и не `%TEMP%\*.py`.

## Workflow агента

```
1. $PYTHON probe.py                          # backends + окна
2. $PYTHON find.py -w "Title" --list         # actionable controls
3. $PYTHON find.py -w "Title" -n "OK" -r button --contains
4. если hits пуст -> добавить --ocr
5. $PYTHON act.py -w "Title" -n "OK" -r button --contains   # dry-run
6. $PYTHON act.py ... --yes                 # только после проверки best/hit
```

Рабочая директория: `skills/win-ui-find/scripts`. На этой машине venv уже можно создать командой выше.

## Команды

### Точное поле по метке (форма 1С)

**Порядок (не координаты):** UIA `EditControl`/`CheckBoxControl` по `Name` →
иначе OCR-метка + sweep `ControlFromPoint`. Писать в поле только если target
`ok` и не огромный `Pane`. Зависимая галка - `--ensure-checkbox` до `--type`.

```powershell
# галка OS-auth по UIA Name (без OCR/right+N)
$PYTHON field_click.py -w "База 5" --checkbox "Аутентификация операционной системы" --ensure-on --yes
# поле + включить галку-зависимость одной командой
$PYTHON field_click.py -w "База 5" -l "Пользователь:" `
  --ensure-checkbox "Аутентификация операционной системы" `
  --type "\\DOMAIN\username" --reject-clip "OtherUser" --yes
# dry-run: куда кликнет
$PYTHON field_click.py -w "База 5" -l "Пользователь:"
# диагностика точки (редко нужна)
$PYTHON coords.py point --x 800 --y 585
```

### Список окон / backends

```powershell
$PYTHON probe.py
```

### Список кликабельных в окне

```powershell
$PYTHON find.py -w "Блокнот" --list --limit 50
```

### Поиск

```powershell
$PYTHON find.py -w "Notepad" -n "Save" -r button --contains
$PYTHON find.py -w "1С" -n "ОК" -r button --contains --ocr
```

Ответ JSON: `ok`, `hits[]`, `best` с полями `source` (`uia`|`ocr`), `cx`/`cy`, `invoke`, bounds.

### Действие (dry-run → `--yes`)

```powershell
$PYTHON act.py -w "Notepad" -n "Save" -r button --contains
$PYTHON act.py -w "Notepad" -n "Save" -r button --contains --yes
$PYTHON act.py -w "Notepad" -n "Edit" -r edit --contains --action type --text "hello" --yes
```

| `--action` | Поведение |
|------------|-----------|
| `click` (default) | Invoke, иначе physical click по центру |
| `invoke` | Только UIA Invoke |
| `type` | ValuePattern / фокус + paste (`--text`) |
| `focus` | SetFocus или клик |

## Роли (`--role`)

`button`, `edit`, `text`, `menuitem`, `checkbox`, `radio`, `listitem`, `tab`, `combo`, `link`, `window`, `pane`, `any`.

## Типичные сбои

| Симптом | Что делать |
|---------|------------|
| Window not found | Уточнить `--window` по `probe.py` / части title |
| UIA пуст / только Minimize-Close (Electron) | дерево урезанное: `--ocr` или DOM/a11y MCP для веба; Chromium: `--force-renderer-accessibility` |
| OCR backend unavailable | `pip install winocr` или `rapidocr-onnxruntime` |
| Клик мимо | DPI/другой монитор: брать `best` из свежего `find.py`, не кэшировать xy надолго |
| Несколько hits | `--index N` в `act.py` или ужесточить `--role` / убрать `--contains` |
| Два окна с одним title | брать окно с max площадью bounds (1С часто так) |
| UIA Name есть, ControlFromPoint другой | клик по OCR `cx`/`cy` строки, не по hit UIA bounds вслепую |
| Кнопки модалки не ButtonControl | OCR текста кнопки в bounds `WindowControl` |
| `SendKeys('{PgDn}')` / модификаторы | PageDown через `keybd_event` VK_NEXT; paste - ValuePattern |

## Off-screen / скролл

UIA может отдавать `Name` у контролов **вне видимой области** (за скроллом).
Для таблиц 1С Taxi ячейки часто `CustomControl` с `Name = "<value> <column>"` -
читать дерево, не полагаться на скрин/OCR. OCR видит только нарисованное.

`winocr.recognize_pil_sync` возвращает **dict** (`lines`, `text`), не объект с `.lines`.
`find.py` OCR предпочитает **sync** API и парсит dict/words; async `recognize_pil`
на Taxi 1С часто давал 0 hits (кнопки вроде «Отправить и получить»).

Перед `ImageGrab` / `--ocr`: **SetFocus** целевого окна 1С/конфигуратора.
Иначе grab может снять Cursor/IDE (в OCR будут CHANGES, Terminal, …).

При матче OCR ужесточать якоря: короткие подстроки (`оборот`, `по счет`) ловят
описания ("оборотах по счетам"). Для заголовков отчётов 1С - `оборотно` + `по счет`.
Окно копии ЕРП: `-w "База 5"` (title `[КОПИЯ] База 5 / 1С:ERP…`).
Дубли имён кнопок (две «Конфигурация»): `act.py --index 1` для toolbar расширений.

## OCR: локально, не VLM

`winocr` / rapidocr крутятся **на машине пользователя**. Это не отправка
скрина в cloud vision ради OCR. Токены чата на сам OCR не списываются;
тратятся, если агент потом кладёт PNG в контекст (Read) или длинный дамп.
В чат предпочитать текстовый JSON/hits, не сырой screenshot.

## SendKeys, раскладка и ввод текста

`SendKeys('^v')` / `'^a'` / `'%d'` / `'+^v'` / `'{Ctrl}v'` на части сеансов
**печатают символы буквально** в Edit (и в 1С, и в `#32770`).

Раскладка RU/EN: ввод через `VkKeyScan` ломается (под RU нет латиницы, под EN
нет кириллицы). Для 1С использовать
`1c-desktop-session/scripts/text_input.py` (`type_text`: ValuePattern →
clipboard+реальный Ctrl+V → SendInput UNICODE). `act.py --action type` зовёт
тот же helper.

## 1С / ЕРП десктоп

Запуск, wait, user, logout, формы/ТЧ/e1cib/печатная форма - skill
**`1c-desktop-session`**. Печатная форма: сначала **сохранить файл**
(`save_print_form` → `#32770` + pypdf), OCR только запасной. Confirm выхода -
внутри `V8TopLevelFrameSDI`.

Полный текст ошибки платформы (длинный stack) - skill **`1c-error-text`**
(клик → Ctrl+A/Ctrl+C → буфер; не OCR). Ввод текста - `text_input.type_text`
из `1c-desktop-session` (не SendKeys, учесть раскладку).

Конфигуратор / 1cestart: skill **`1c-desktop-session`**.  
Список ИБ: не плодить `1cestart`; диалог **Изменить...** искать
EnumWindows + `ControlFromHandle` (`V8TopLevelFrameSDIsec`), не только
GetChildren. F7 / СиО / сравнения `.cfe` - там же.

Модалки 1С-Отчетности («Новые сообщения…») часто **внутри** main
`V8TopLevelFrameSDI` (отдельного top-window в probe нет). Esc работает
при фокусе 1С (`SetActive` + клик по модалке); без фокуса клавиша уходит
в Cursor. Запасной путь: `-w "КОПИЯ"` → `Закрыть` index 0 (physical click).
Полный desktop UIA-обход не нужен и зависает. Поля 1С читать/писать -
`text_input` Ctrl+A (skill `1c-desktop-session`), не OCR значения.

Здесь только общий UIA/OCR UI.

## Чего не делать

- Не гонять полный скрин через vision-модель для координат
- Не делать `act.py --yes` без просмотра dry-run JSON
- Не искать текст по всему экрану без `-w`
- Не обходить весь desktop UIA (`GetRootControl` children) «на всякий случай»
  - зависание; scope = конкретное `-w`
- Не открывать 1С кликами/VLM - см. `1c-desktop-session`
- Не запускать конфигуратор (DESIGNER) на прод - только копии/локаль
  (`1c-designer-no-prod`, гейт в `open_1c.py`)
