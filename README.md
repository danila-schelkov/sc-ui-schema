# Supercell’s *.ui (TOML) files schema

[EN](./docs/README.en.md)

Схема для валидации UI TOML файлов из игр Supercell.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Использование

Вы можете использовать `https://ext.nulls.gg/mods/schema/ui.schema.json` в качестве `$schema` в любом JSON или TOML файле.

### Локально

Для использования локальной схемы выполните все шаги из раздела [Разработка](#разработка) и укажите полный путь до `ui.schema.json` в `$schema` с помощью [file:/// URI](https://ru.wikipedia.org/wiki/File_(%D1%81%D1%85%D0%B5%D0%BC%D0%B0_URI)) любого JSON или TOML файла.

## Валидация

> [!NOTE]
> На данный момент параллельно существует две версии валидатора, одна — на Python 3, другая — на Rust. Ниже описывается версия на Python 3.

Для валидации TOML-файлов UI-схем используется специальный пайплайн, так как [taplo](https://taplo.tamasfe.dev/) не поддерживает современные стандарты JSON Schema.

### Как это работает

Пайплайн валидации реализован в скрипте [`validate.py`](./validate.py) и состоит из следующих шагов:

1. Чтение `.ui` файлов — скрипт рекурсивно находит все файлы с расширением `.ui` (TOML-формат).
2. Конвертация TOML → JSON — с помощью встроенного модуля Python `tomllib` TOML-файлы преобразуются в JSON.
3. В конвертированный JSON автоматически добавляется поле `$schema`, указывающее на локальную схему (`src/ui.schema.json`).
4. Валидация — полученный JSON-файл валидируется с помощью [`jsonschema`](https://github.com/sourcemeta/jsonschema) — CLI-инструмента от Sourcemeta, который корректно обрабатывает современные стандарты JSON Schema, в отличие от taplo.
5. Если валидация прошла успешно, то временный `.json` файл удаляется, если нет, то вы можете открыть его любым редактором, который поддерживает json schema и посмотреть, в чём заключается ошибка. Текст ошибки также будет продублирован в консоли.

### Запуск

```sh
python3 validate.py  # или
cargi run  # или
make validate
```

Скрипт обработает все `.ui` файлы в текущей директории и выведет ошибки валидации, если они есть.

> [!NOTE] 
> `jsonschema` CLI должен быть установлен отдельно. Установить можно через `npm install -g @sourcemeta/jsonschema`.

## Семантическая валидация

Помимо проверки структуры файла по JSON Schema, выполняется семантическая валидация — проверка логической корректности ссылок и зависимостей между файлами.

### Что проверяется

- BindingId — все ссылки на bindingId разрешаются в контексте текущего файла:
  - Прямые bindings из секции `bindings`
  - Bindings из файлов, указанных в `copy_configs`
  - Bindings из файлов с тем же `sc_file_asset_id_list` (источник AssetIdList)
  - Bindings из файлов, на которые ссылается `sc_file` (источник OtherTomlConfig)

- AnimationKey — все ссылки на анимации проверяются:
  - Прямые анимации из секций `animation` или `animations`
  - Анимации из файлов, указанных в `copy_configs`

- Кросс-файловые ссылки — registry всех загруженных `.ui` файлов используется для разрешения ссылок между файлами без повторного парсинга.

> [!TIP]
> Для запуска только семантической валидации пропустите проверку JSON Schema:
> ```sh
> python3 validate.py --skip-schema-validation  # или
> python3 validate.py -s  # или
> ```
> 
> ```sh
> cargo run -- --skip-schema-validation  # или
> cargo run -- -s
> ```

## Разработка

```sh
git clone https://github.com/danila-schelkov/sc-ui-schema
cd sc-ui-schema
```

### Публикация

Для публикации можно собрать minified версию схемы. 

```sh
python3 build.py  # или
make
```

В результате выполнения скрипта в папке `build/` будет находиться готовый bundle схемы.

## TODO

- [ ] Загрузка `.sc` файлов для `ClientFile` — при использовании `sc_file_source = "ClientFile"` необходимо загружать и парсить соответствующие `.sc` файлы для полной валидации.
- [ ] Валидация ссылок на clip frame — проверка корректности ссылок на clip frame в child references.
- [ ] Разрешение bindings из `.sc` файлов — извлечение и валидация bindings, определённых в `.sc` файлах, для полных семантических проверок.

## О схеме

Мы используем Draft 2020-12 в качестве языка для описания JSON Schema, но так как JSON Schema Validator у VS Code не поддерживает версии выше draft-07, скорее всего оно не будет работать правильным образом.

Больше информации о спецификации JSON Schema на сайте: https://json-schema.org/specification

## Лицензия

Этот проект распространяется по лицензии MIT ([LICENSE](/LICENSE) или https://opensource.org/licenses/MIT).  

## Дисклеймер

Эта JSON-схема является независимым проектом, разработанным сообществом, и не аффилирована с компанией Supercell, не одобрена и не спонсируется ею. Supercell не несёт за неё ответственности. Этот инструмент предназначен исключительно для образовательных целей и фанатской разработки.

<!-- 
Установить Even Better TOML: link TBD

`.taplo.toml`:
```toml
include = ["*.toml", "**/*.ui"]

[[rule]]
include = ["**/*.ui"]

[rule.schema]
path = "./ui.schema.json"
enabled = true
```
-->