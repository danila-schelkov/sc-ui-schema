# Supercell’s *.ui (TOML) files schema

[EN](./docs/README.en.md)

Схема для валидации UI TOML файлов из игр Supercell.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Использование

Вы можете использовать `https://ext.nulls.gg/mods/schema/ui.schema.json` в качестве `$schema` в любом JSON или TOML файле.

### Локально

Для использования локальной схемы выполните все шаги из раздела [Разработка](#разработка) и укажите полный путь до `ui.schema.json` в `$schema` с помощью [file:/// URI](https://ru.wikipedia.org/wiki/File_(%D1%81%D1%85%D0%B5%D0%BC%D0%B0_URI)) любого JSON или TOML файла.

## Валидация

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
make validate
```

Скрипт обработает все `.ui` файлы в текущей директории и выведет ошибки валидации, если они есть.

> [!NOTE] 
> `jsonschema` CLI должен быть установлен отдельно. Установить можно через `npm install -g @sourcemeta/jsonschema`.

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