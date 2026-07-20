# Null's Mods JSON Schema

[EN](./docs/README.en.md)

Схема для валидации JSON файлов модификаций Null's Mods.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Использование

Вы можете использовать `https://ext.nulls.gg/mods/schema/schema.json` в качестве `$schema` в любом JSON файле.

При написании patches-файлов (те, что указываются в `@patches`) используйте `https://ext.nulls.gg/mods/schema/patches.schema.json`.

### Локально

Для использования локальной схемы выполните все шаги из раздела [Разработка](#разработка) и укажите полный путь до `schema.json` в `$schema` с помощью [file:/// URI](https://ru.wikipedia.org/wiki/File_(%D1%81%D1%85%D0%B5%D0%BC%D0%B0_URI)) любого JSON файла.

При написании patches-файлов (те, что указываются в `@patches`) используйте полный путь до `patches.schema.json`.

## Разработка

```sh
git clone https://github.com/nulls-mods-community/schema
cd schema
```

Положите все .csv файлы в папку `csv/` в корне проекта и сгенерируйте из них схемы для патчей.

```sh
python3 generate.py  # или
make generate
```

### Публикация

Для публикации можно собрать minified версию схемы. При этом скрипт `generate.py` выполнять не обязательно.

```sh
python3 build.py  # или
make
```

В результате выполнения скрипта в папке `build/` будет находиться готовый bundle с сгенерированными схемами.

## О схеме

Мы используем draft-07 в качестве языка для описания JSON Schema, так как JSON Schema Validator у VS Code не поддерживает версии выше.

Также эта схема описывает некоторые snippets, которые специфичны для VS Code и не предполагаются форматом JSON Schema. Подробнее о VS Code-specific функуциях можно узнать здесь: https://code.visualstudio.com/docs/languages/json ([Archive](https://web.archive.org/web/20250914171533/https://code.visualstudio.com/docs/languages/json))

Больше информации о спецификации JSON Schema на сайте: https://json-schema.org/specification

## Подсказки имен из таблиц.

Текущая схема позволяет инструментам работы с JSON Schema подсказывать имена `properties` (колонок таблиц), а также предполагает подсказки `propertyNames` (строк таблиц) через `examples`. 

### Патч VS Code

Начиная с VS Code 1.105.0 появились подсказки `propertyNames` через `examples`, этот раздел устарел.

<details>
<summary>Для более версий 1.104.0 и ниже</summary>

В VS Code 1.104.0 и ниже не реализованы подсказки `propertyNames` через `examples`. 

Если вы хотите использовать эту схему полноценно, то можете добавить в свой VS Code эту функцию с помощью небольшого патча.

В файле `%LOCALAPPDATA%\Programs\Microsoft VS Code\resources\app\extensions\json-language-features\server\dist\node\875.jsonServerMain.js` после 207547 символа нужно вставить этот кусочек кода:
```js
if(n.examples)for(let t=0;t<n.examples.length;t++){e(n.examples[t],undefined,undefined,undefined)};
```

</details>

## Embedded-patches

Смотреть секцию [Поддержка embedded-patches](#поддержка-embedded-patches). В качестве текущего решения выбран второй пункт: файл общей схемы генерируется из нескольких отдельных схем с помощью шаблона `schema.json`, который не запрещает использование `additionalProperties`. При сборке схемы с помощью `build.py` шаблон разворачивается в схему с ограниченным `additionalProperties`.

## TODO

### Поддержка массивов

На данный момент в схеме patches отсутствуют массивы в качестве значений. Предполагается позже добавить их, но не во все места, а только туда, где это имеет смысл. В качестве MVP найти массивы с помощью скрипта (у строк с пустой первой колонкой смотреть какие колонки НЕ пустые).

### Поддержка embedded-patches

Из-за недостатков draft-07 общая схема для мода генерируется с помощью Python-скрипта, так как нельзя одновременно объединить разные схемы (`allOf`) и запретить `additionalProperties` (лишние параметры). Это может измениться в будущем.

Возможные решения:

1) Позволить вкладывать патчи в `@patches`, в таком случае можно отказаться от префикса @, так как все корневые теги станут мета-тегами.
2) Автоматически генерировать файл с объединенным обьявлением `properties` из `generated/patches.schema.json` и `schema.json`, `feature.schema.json`. В таком случае можно будет позволить пользователю выбрать нужную версию самостоятельно. Также это позволит оставить запрет на `additionalProperties`.
3) Использовать `allOf`, позволив использовать лишние параметры.

## Идеи

### Требовать ли `@enabled`, если присутствует не пустой `@conflicts`?

Фактически такого требования не существует на момент подписи модификации. Обязательным является только разрешить все конфликты перед подписью. Однако если заставить использовать `@enabled`, то это хорошо скажется на удобстве разработки, так как пользователи будут сразу разрешать конфликты. 

## Лицензия

Этот проект распространяется по лицензии MIT.  
Полный текст лицензии доступен в файле [LICENSE](./LICENSE).


---

Установить Even Better TOML: link TBD

добавьте файлик 

`.taplo.toml`:
```toml
include = ["*.toml", "**/*.ui"]

[[rule]]
include = ["**/*.ui"]

[rule.schema]
path = "./ui.schema.json"
enabled = true
```