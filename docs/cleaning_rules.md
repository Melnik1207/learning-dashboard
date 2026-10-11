# Правила очищення `dim_course`

**Версія:** 1.0
**Датасет:** `ai_course_homework1/dim_course.csv` — 3 032 рядки × 21 колонка
**Зріз:** 2026-08-15
**Базовий аналіз:** `ai_course_homework1/reports/data_profile.md`

---

## Загальні умови виконання

1. Вхідний файл не редагується. Усі зміни застосовуються до робочої копії або цільової таблиці сховища.
2. Правила виконуються строго в порядку з розділу «Послідовність виконання». Дві залежності є критичними і позначені явно.
3. Після кожного правила запускається його блок `Validation check`. Якщо хоч одна перевірка не проходить — наступне правило не виконується.
4. Кількісні очікування наведені для зрізу 2026-08-15. При іншому зрізі числа зміняться, логіка перевірок — ні.
5. Вісім колонок у `dim_course` є денормалізованими копіями з `dim_specialization`: `specialization_url`, `specialization_name`, `spec_size`, `partner`, `level`, `domain`, `specialization_enrolled`, `review_score`. Правити їх безпосередньо в `dim_course` заборонено — тільки в джерелі з подальшим перезаливом.

## Зведена таблиця правил

| ID | Правило | Тип операції | Зачіпає рядків | Блокує наступні |
|----|---------|--------------|----------------|-----------------|
| R3.1 | Обрізання пробілів | зміна значень | 162 | так, R4 |
| R1 | Додати `course_slug` | додавання колонки | 3 032 | ні |
| R2 | Маскований NULL → NULL | зміна значень | 3 111 | ні |
| R4 | Мова курсу в окреме поле | додавання колонок | 171 | ні |
| R3.2 | Зміна типів | зміна схеми | 3 019 | так, фінальне |

---

# R1. Додати `course_slug` як ключ курсу

## Мета

Отримати ідентифікатор, який розпізнає курс. Наявний `course_id` для цього непридатний: він унікальний на кожному рядку і видає один курс, що входить у кілька програм, за кілька різних курсів. Реальних курсів 2 917, рядків 3 032.

## Інструкції

1. Створити текстову колонку `course_slug`, `VARCHAR(100)`.
2. Заповнити фрагментом `course_url` між `/learn/` і першим знаком питання.
3. Побудувати індекс по `course_slug`.
4. Наявні колонки не змінювати — правило суто додаткове.

```sql
ALTER TABLE dim_course ADD COLUMN course_slug VARCHAR(100);

UPDATE dim_course
SET course_slug = SPLIT_PART(SPLIT_PART(course_url, '/learn/', 2), '?', 1);

CREATE INDEX idx_course_slug ON dim_course (course_slug);
```

## Умова перевірки

Правило застосовне, якщо структура URL відповідає очікуваній. Перевіряти **до** заповнення:

```sql
-- 1. Усі URL починаються з /learn/
SELECT COUNT(*) FROM dim_course WHERE course_url NOT LIKE '/learn/%';

-- 2. Усі URL містять query-параметр
SELECT COUNT(*) FROM dim_course WHERE course_url NOT LIKE '%?%';

-- 3. Немає завершального слеша перед знаком питання
SELECT COUNT(*) FROM dim_course WHERE course_url ~ '/\?';
```

Очікується нуль у всіх трьох. На зрізі 2026-08-15 умова виконується.

## Дія при порушенні

| Порушення | Дія |
|---|---|
| URL не починається з `/learn/` | **Зупинити правило.** Змінився формат джерела — переглянути логіку розбору, не заповнювати колонку частково |
| URL без `?` | Заповнити slug усім фрагментом після `/learn/`, позначити рядок `slug_source = 'no_query'` для ручної перевірки |
| Завершальний слеш | Додати обрізання слеша в розбір: `RTRIM(..., '/')`, перезапустити правило |
| Після заповнення знайдено значення з символами поза `[a-z0-9-]` | Не виправляти автоматично. Вивести список у лог, передати на ручну перевірку — ймовірно, зміна схеми URL на джерелі |
| Порожній slug при непорожньому URL | **Зупинити правило**, розібратися з конкретним рядком |

## Змінені колонки

| Колонка | Операція | Тип | Було | Стало |
|---|---|---|---|---|
| `course_slug` | **додано** | `VARCHAR(100)` | — | 3 032 значення, 2 917 унікальних |

Змінених або видалених колонок немає.

## Validation check

```sql
-- V1.1 Повнота заповнення. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE course_slug IS NULL OR course_slug = '';

-- V1.2 Набір символів. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE course_slug !~ '^[a-z0-9-]+$';

-- V1.3 Кардинальність. Очікується: 2917
SELECT COUNT(DISTINCT course_slug) FROM dim_course;

-- V1.4 Довжина в межах норми. Очікується: min=2, max=80
SELECT MIN(LENGTH(course_slug)), MAX(LENGTH(course_slug)) FROM dim_course;

-- V1.5 Зворотна відповідність: один slug -> одна назва. Очікується: 0
SELECT COUNT(*) FROM (
  SELECT course_slug FROM dim_course
  GROUP BY course_slug HAVING COUNT(DISTINCT BTRIM(course)) > 1
) t;
```

**Критичність:** V1.1, V1.2, V1.3 — блокуючі. V1.4, V1.5 — попереджувальні, логувати й продовжувати.

---

# R2. Маскований NULL перевести в справжній NULL

## Мета

Прибрати значення, які виглядають заповненими, але позначають відсутність даних. Стандартна перевірка на пропуски їх не бачить: у `level` немає жодного NULL, хоча 196 рядків не мають реального рівня.

## Інструкції

1. У `dim_specialization` замінити на NULL значення `level`, що дорівнюють `'Unknown'`.
2. Там само замінити на NULL значення `domain`, що дорівнюють `'Unknown'`.
3. Перезалити спец-атрибути з `dim_specialization` у `dim_course`.
4. У `dim_course` замінити на NULL значення `n_reviews`, що дорівнюють нулю **за умови** порожнього `mean_stars`.
5. Рядки, де `n_reviews = 0`, а `mean_stars` заповнений, не чіпати.

```sql
-- Кроки 1-2: у джерелі
UPDATE dim_specialization SET level  = NULL WHERE level  = 'Unknown';
UPDATE dim_specialization SET domain = NULL WHERE domain = 'Unknown';

-- Крок 4: у dim_course
UPDATE dim_course SET n_reviews = NULL
WHERE n_reviews = 0 AND mean_stars IS NULL;
```

**Обґрунтування кроку 4.** Нуль у `n_reviews` означає «відгуки не збирали», а не «відгуків немає». Доказ: `mean_stars` і `pct_low` заповнені рівно там і тільки там, де `n_reviews > 0` — без винятків на 3 032 рядки. Без правила `AVG(n_reviews)` показує 52 замість реальних 624.

## Умова перевірки

```sql
-- 1. Очікувана кількість маркерів перед очищенням
SELECT
  COUNT(*) FILTER (WHERE level  = 'Unknown') AS lvl,    -- очікується 196
  COUNT(*) FILTER (WHERE domain = 'Unknown') AS dom,    -- очікується 137
  COUNT(*) FILTER (WHERE level = 'Unknown' AND domain = 'Unknown') AS both  -- 0
FROM dim_course;

-- 2. Інваріант звʼязку n_reviews <-> mean_stars. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE (n_reviews > 0) <> (mean_stars IS NOT NULL);

-- 3. Інші маркери-заглушки, яких не мало б бути. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE BTRIM(level)  IN ('N/A','NA','-','','null','none','unknown')
   OR BTRIM(domain) IN ('N/A','NA','-','','null','none','unknown');
```

## Дія при порушенni

| Порушення | Дія |
|---|---|
| Кількість `Unknown` відрізняється від очікуваної | Не блокувати. Залогувати фактичне число, виконати правило, внести нове очікування у версію правил |
| Знайдено рядки з `n_reviews = 0` і заповненим `mean_stars` | **Зупинити правило.** Інваріант зламано — нуль тут може означати справжній нуль. Передати на зʼясування джерела, до відповіді `n_reviews` не чіпати |
| Знайдено рядки з `n_reviews > 0` і порожнім `mean_stars` | Те саме — зупинити, зʼясувати |
| Знайдено нові маркери-заглушки (`N/A`, `-`, `none`) | Розширити список умов очищення, перезапустити правило |
| `level` або `domain` різняться всередині одного `spec_id` | **Зупинити правило.** Порушено денормалізацію; спершу відновити узгодженість із `dim_specialization` |
| Після перезаливу значення в `dim_course` не збігаються з `dim_specialization` | **Зупинити.** Перезалив пройшов частково — повторити повністю |

## ЗАБОРОНЕНО

- **Імпутувати `level` модою або будь-яким іншим значенням.** Курси без мітки рівня тривають у середньому 746 хвилин проти 444–502 у розмічених. Мітка губиться саме у великих і складних курсів, тобто пропуск не випадковий (MNAR). Заповнення значенням `Beginner` систематично занизить складність найважчої частини каталогу.
- Допустима альтернатива, якщо порожнє значення незручне для вітрини: окрема категорія `unlabeled`, візуально відмінна від справжніх рівнів.
- **Замінювати `n_reviews = 0` на NULL без перевірки `mean_stars`.** Умова обовʼязкова — вона єдина відрізняє «не збирали» від можливого справжнього нуля.

## Змінені колонки

| Колонка | Таблиця | Операція | Рядків | Було | Стало |
|---|---|---|---|---|---|
| `level` | `dim_specialization` → перезалив | значення | 196 | `'Unknown'` | `NULL` |
| `domain` | `dim_specialization` → перезалив | значення | 137 | `'Unknown'` | `NULL` |
| `n_reviews` | `dim_course` | значення | 2 778 | `0` | `NULL` |

Колонки `mean_stars` і `pct_low` не змінюються — вони вже коректно порожні.

## Validation check

```sql
-- V2.1 Маркери прибрані. Очікується: 0
SELECT COUNT(*) FROM dim_course WHERE level = 'Unknown' OR domain = 'Unknown';

-- V2.2 Пропуски стали видимими. Очікується: 196 і 137
SELECT
  COUNT(*) FILTER (WHERE level  IS NULL) AS lvl_null,
  COUNT(*) FILTER (WHERE domain IS NULL) AS dom_null
FROM dim_course;

-- V2.3 ГОЛОВНИЙ ІНВАРІАНТ: n_reviews і mean_stars порожні строго разом. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE (n_reviews IS NULL) <> (mean_stars IS NULL);

-- V2.4 Кількість непорожніх відгуків не змінилась. Очікується: 254
SELECT COUNT(*) FROM dim_course WHERE n_reviews IS NOT NULL;

-- V2.5 Середнє перерахувалось коректно. Очікується ~624, а не ~52
SELECT ROUND(AVG(n_reviews), 1) FROM dim_course;

-- V2.6 Звірка копій із джерелом. Очікується: 0
SELECT COUNT(*) FROM dim_course c
JOIN dim_specialization s USING (spec_id)
WHERE c.level IS DISTINCT FROM s.level
   OR c.domain IS DISTINCT FROM s.domain;
```

**Критичність:** V2.1, V2.3, V2.6 — блокуючі. V2.2, V2.4, V2.5 — контрольні, розбіжність логувати.

---

# R3. Нормалізувати текст і типи

Правило складається з двох частин, які виконуються на різних етапах: **3.1 — першою серед усіх правил**, **3.2 — останньою**.

---

## R3.1. Обрізання пробілів

### Мета

Усунути хвостові пробіли, які ламають джойни за назвою, дедуплікацію і створюють фантомні дублі в довідниках BI.

### Інструкції

1. Обрізати початкові й кінцеві пробіли в `course` — у `dim_course`.
2. Обрізати початкові й кінцеві пробіли в `partner` — у `dim_specialization`, з подальшим перезаливом.
3. Внутрішні подвійні пробіли не чіпати — їх у датасеті немає.

```sql
UPDATE dim_course         SET course  = BTRIM(course)  WHERE course  <> BTRIM(course);
UPDATE dim_specialization SET partner = BTRIM(partner) WHERE partner <> BTRIM(partner);
```

### Умова перевірки

```sql
-- Очікується: course = 122, partner = 40
SELECT
  COUNT(*) FILTER (WHERE course  <> BTRIM(course))  AS course_ws,
  COUNT(*) FILTER (WHERE partner <> BTRIM(partner)) AS partner_ws,
  COUNT(*) FILTER (WHERE course  LIKE '%  %')       AS course_double,
  COUNT(*) FILTER (WHERE course  ~ '[\t\r\n]')      AS course_ctrl
FROM dim_course;
```

### Дія при порушенні

| Порушення | Дія |
|---|---|
| Знайдено подвійні пробіли всередині рядка | Розширити правило: `REGEXP_REPLACE(course, '\s+', ' ', 'g')`. Спершу вивести список — можливо, це частина оригінальної назви |
| Знайдено табуляції або переноси рядків | **Зупинити правило.** Ознака зламаного парсингу CSV — перевірити завантаження, а не чистити наслідки |
| Після обрізання зʼявились порожні назви | **Зупинити.** Назва не може бути порожньою, це втрата даних |
| Після обрізання зросла кількість дублікатів назв | Очікувано і нормально. Залогувати нове число, продовжити |

### Змінені колонки

| Колонка | Таблиця | Операція | Рядків |
|---|---|---|---|
| `course` | `dim_course` | значення | 122 |
| `partner` | `dim_specialization` → перезалив | значення | 40 |

### Validation check

```sql
-- V3.1.1 Пробілів не лишилось. Очікується: 0
SELECT COUNT(*) FROM dim_course WHERE course <> BTRIM(course);
SELECT COUNT(*) FROM dim_specialization WHERE partner <> BTRIM(partner);

-- V3.1.2 Порожніх назв немає. Очікується: 0
SELECT COUNT(*) FROM dim_course WHERE course = '' OR course IS NULL;

-- V3.1.3 Кількість рядків не змінилась. Очікується: 3032
SELECT COUNT(*) FROM dim_course;
```

---

## R3.2. Зміна типів

### Мета

Привести типи у відповідність до фактичних значень. Виконується **після всіх інших правил**, коли значення вже фінальні.

### Інструкції

1. Привести `n_weeks` до `SMALLINT`.
2. Привести `practice_min` до `INTEGER`.
3. Закріпити текстовий тип `VARCHAR(8)` для `course_id` і `spec_id` у схемі завантаження.
4. Не змінювати `total_minutes`, `video_min`, `minutes_per_week` — там є справжні дробові значення.

```sql
ALTER TABLE dim_course
  ALTER COLUMN n_weeks      TYPE SMALLINT USING n_weeks::smallint,
  ALTER COLUMN practice_min TYPE INTEGER  USING practice_min::integer;
```

### Умова перевірки

```sql
-- Обидва мають повернути 0 перед зміною типу
SELECT COUNT(*) FROM dim_course WHERE n_weeks      IS NOT NULL AND n_weeks      % 1 <> 0;
SELECT COUNT(*) FROM dim_course WHERE practice_min IS NOT NULL AND practice_min % 1 <> 0;

-- Діапазон вміщається в SMALLINT. Очікується: 1 і 13
SELECT MIN(n_weeks), MAX(n_weeks) FROM dim_course;

-- Контроль: дробові значення справді є там, де тип лишається float. Очікується: >0
SELECT COUNT(*) FROM dim_course WHERE video_min % 1 <> 0;
```

### Дія при порушенні

| Порушення | Дія |
|---|---|
| Знайдено дробові `n_weeks` або `practice_min` | **Не приводити тип.** Округлення знищить дані. Вивести список, передати на зʼясування — можливо, змінилась методика збору |
| `MAX(n_weeks)` перевищує 32 767 | Використати `INTEGER` замість `SMALLINT` |
| `video_min` виявився цілим у всіх рядках | Залогувати. Можлива втрата точності на джерелі — перевірити перед зміною типу цієї колонки |
| Приведення типу впало на конкретному рядку | **Відкотити транзакцію повністю.** Часткова зміна типу неприпустима |

### Змінені колонки

| Колонка | Операція | Було | Стало |
|---|---|---|---|
| `n_weeks` | тип | `FLOAT` | `SMALLINT` |
| `practice_min` | тип | `FLOAT` | `INTEGER` |
| `course_id` | тип (фіксація) | не визначений | `VARCHAR(8)` |
| `spec_id` | тип (фіксація) | не визначений | `VARCHAR(8)` |

### Validation check

```sql
-- V3.2.1 Типи застосовані
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'dim_course'
  AND column_name IN ('n_weeks','practice_min','course_id','spec_id');
-- Очікується: smallint, integer, character varying, character varying

-- V3.2.2 Значення не спотворені. Очікується: 4 і 135
SELECT
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY n_weeks),
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY practice_min)
FROM dim_course;

-- V3.2.3 Ведучі нулі на місці. Очікується: 'C00000' і 'S0000'
SELECT MIN(course_id), MIN(spec_id) FROM dim_course;

-- V3.2.4 Кількість непорожніх не змінилась. Очікується: 3019
SELECT COUNT(n_weeks), COUNT(practice_min) FROM dim_course;
```

---

# R4. Винести мову курсу в окреме поле

## Мета

Відокремити мову локалізації від назви курсу. Без цього пошук за назвою не знаходить переклад, а у звіті «топ курсів» один курс розмазується на кілька мовних версій.

**Передумова:** виконувати тільки після R3.1. Маркери стоять у кінці рядка — хвостовий пробіл зламає розпізнавання.

## Інструкції

1. Створити колонку `language`, `VARCHAR(5)`, значення за замовчуванням `'en'`.
2. Створити колонку `course_title_base`, `VARCHAR(255)`.
3. Перевірити назву на збіг із маркером **у кінці рядка**, без урахування регістру. Перший збіг визначає мову.
4. Рядкам без збігу присвоїти `'en'`.
5. Записати в `course_title_base` назву без маркера, з обрізаними пробілами.
6. Колонку `course` не змінювати — вона лишається оригінальною публічною назвою.

### Словник маркерів

| Маркер у кінці назви | `language` | Очікувано рядків |
|---|---|---|
| `日本語版` | `ja` | 53 |
| `Español` / `Espanol`, з необовʼязковим `en` попереду | `es` | 42 |
| `Français` / `Francais`, з необовʼязковим `en` попереду | `fr` | 36 |
| `Português` / `Portugues`, з необовʼязковими `em` попереду і `Brasileiro` після | `pt-BR` | 22 |
| `한국어` / `한국어판` | `ko` | 8 |
| `dalam bahasa Indonesia` | `id` | 5 |
| `italiano`, з необовʼязковим `in` попереду | `it` | 5 |
| `中文` / `中文版` / `简体中文` | `zh` | 0 |
| `Deutsch`, з необовʼязковим `auf` попереду | `de` | 0 |
| `на русском` | `ru` | 0 |

Останні три в поточному зрізі відсутні — залишені для майбутніх завантажень.

**Разом:** 171 локалізований рядок на 151 унікальному slug, 2 861 рядок `en`.

```sql
ALTER TABLE dim_course
  ADD COLUMN language          VARCHAR(5) NOT NULL DEFAULT 'en',
  ADD COLUMN course_title_base VARCHAR(255);

UPDATE dim_course SET language =
  CASE
    WHEN course ~* '日本語版$'                                THEN 'ja'
    WHEN course ~* '(en\s+)?Espa(ñ|n)ol$'                     THEN 'es'
    WHEN course ~* '(en\s+)?Fran(ç|c)ais$'                    THEN 'fr'
    WHEN course ~* '(em\s+)?Portugu(ê|e)s(\s+Brasileiro)?$'   THEN 'pt-BR'
    WHEN course ~* '한국어(판)?$'                              THEN 'ko'
    WHEN course ~* 'dalam\s+bahasa\s+Indonesia$'              THEN 'id'
    WHEN course ~* '(in\s+)?italiano$'                        THEN 'it'
    WHEN course ~* '(简体)?中文(版)?$'                         THEN 'zh'
    WHEN course ~* '(auf\s+)?Deutsch$'                        THEN 'de'
    WHEN course ~* 'на\s+русском$'                            THEN 'ru'
    ELSE 'en'
  END;

UPDATE dim_course SET course_title_base = BTRIM(REGEXP_REPLACE(course,
  '\s*(日本語版|(en\s+)?Espa(ñ|n)ol|(en\s+)?Fran(ç|c)ais|(em\s+)?Portugu(ê|e)s(\s+Brasileiro)?|한국어(판)?|dalam\s+bahasa\s+Indonesia|(in\s+)?italiano|(简体)?中文(版)?|(auf\s+)?Deutsch|на\s+русском)$',
  '', 'i'));
```

## Умова перевірки

```sql
-- 1. Назви з не-ASCII символами, не покриті словником.
--    Виявляє нові мови та курси, написані цільовою мовою повністю.
SELECT course_slug, course FROM dim_course
WHERE course ~ '[^\x00-\x7F]' AND language = 'en'
  AND course !~ '[’‘“”–—®™]';   -- виключити типографіку і знаки торгових марок

-- 2. Перехресна звірка зі slug: локалізований курс має мовний суфікс.
--    Очікується: 0 (усі 171 збігаються)
SELECT course_slug, course, language FROM dim_course
WHERE language <> 'en'
  AND SUBSTRING(course_slug FROM '-([a-z]{2,3})$') IS NULL;

-- 3. Зворотний напрямок: slug натякає на мову, а назва — ні.
--    Очікується 5 ВІДОМИХ хибних спрацювань, перелічені нижче
SELECT course_slug, course FROM dim_course
WHERE language = 'en'
  AND SUBSTRING(course_slug FROM '-(jp|ja|es|fr|br|pt|ko|it|ita|id|de|ru|zh)$') IS NOT NULL;
```

### Відомі хибні спрацювання перевірки 3

Ці пʼять рядків **англомовні**, суфікс у slug є частиною звичайного слова. Вносити їх у виняток, не позначати як локалізовані:

| `course_slug` | Назва |
|---|---|
| `your-world-and-what-shapes-it` | Your World and What Shapes **It** |
| `our-world-and-how-to-accept-it` | Our World and How to Accept **It** |
| `their-world-and-how-you-define-it` | 'Their' World and How You Define **It** |
| `data-what-it-is-what-can-we-do-with-it` | Data – What It Is, What We Can Do With **It** |
| `android-graphics-opengl-es` | Android Graphics with OpenGL **ES** |

## Дія при порушенні

| Порушення | Дія |
|---|---|
| Перевірка 1 виявила назви з не-ASCII поза словником | Проаналізувати вручну. Якщо це нова мова — розширити словник і перезапустити. Якщо курс повністю написаний іншою мовою без маркера — завести окремий список винятків, **не** вгадувати мову за символами |
| Перевірка 2 дала ненульовий результат | Залогувати як попередження, не блокувати. Означає або новий формат slug, або маркер у назві без відповідного суфікса. Перевірити вручну |
| Перевірка 3 дала більше 5 рядків | Порівняти з таблицею відомих винятків. Нові рядки — на ручну перевірку: можливо, пропущений маркер у назві |
| `course_title_base` вийшов порожнім | **Зупинити правило.** Назва складалася лише з маркера — ознака зламаних даних |
| Після очищення `course_title_base` збігся у курсів різних партнерів | Нормально і очікувано. Це різні курси з однаковою базовою назвою — див. заборону нижче |
| Розподіл `language` суттєво відрізняється від очікуваного | Не блокувати. Залогувати фактичний розподіл, оновити очікування у версії правил |

## ЗАБОРОНЕНО

- **Визначати мову за суфіксом `course_slug`.** Дає хибні спрацювання на англійських назвах (див. таблицю вище). Крім того, суфікси нестабільні: японська кодується і як `-jp`, і як `-ja`; португальська — `-br` і `-pt`; італійська — `-it` і `-ita`; а курс `gcp-fundamentals-core-infrastructure-id` має взагалі інший корінь. Slug використовується **тільки** як контрольна перевірка.
- **Зливати переклади в один курс.** Це окремі продукти з окремими slug і окремою аудиторією. `course_title_base` призначений для групування у звітах, не для дедуплікації.
- **Змінювати колонку `course`.** Оригінальна публічна назва має зберігатися незмінною.

## Змінені колонки

| Колонка | Операція | Тип | Значення |
|---|---|---|---|
| `language` | **додано** | `VARCHAR(5) NOT NULL DEFAULT 'en'` | `en` 2 861, `ja` 53, `es` 42, `fr` 36, `pt-BR` 22, `ko` 8, `id` 5, `it` 5 |
| `course_title_base` | **додано** | `VARCHAR(255)` | 3 032 значення, у 171 рядку відрізняється від `course` |

Колонка `course` не змінюється.

## Validation check

```sql
-- V4.1 Розподіл мов
SELECT language, COUNT(*) FROM dim_course GROUP BY 1 ORDER BY 2 DESC;
-- Очікується: en 2861, ja 53, es 42, fr 36, pt-BR 22, ko 8, id 5, it 5

-- V4.2 Базова назва заповнена скрізь. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE course_title_base IS NULL OR course_title_base = '';

-- V4.3 Для en базова назва дорівнює оригіналу. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE language = 'en' AND course_title_base <> course;

-- V4.4 Для не-en базова назва коротша за оригінал. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE language <> 'en' AND LENGTH(course_title_base) >= LENGTH(course);

-- V4.5 Перехресна звірка зі slug. Очікується: 171 з 171
SELECT COUNT(*) FROM dim_course
WHERE language <> 'en'
  AND SUBSTRING(course_slug FROM '-([a-z]{2,3})$') IS NOT NULL;

-- V4.6 Маркерів у базовій назві не лишилось. Очікується: 0
SELECT COUNT(*) FROM dim_course
WHERE course_title_base ~* '(日本語版|Espa(ñ|n)ol|Fran(ç|c)ais|Portugu(ê|e)s|한국어|italiano|bahasa\s+Indonesia)$';

-- V4.7 Кількість локалізованих. Очікується: 171 рядок, 151 slug
SELECT COUNT(*), COUNT(DISTINCT course_slug) FROM dim_course WHERE language <> 'en';
```

**Критичність:** V4.2, V4.3, V4.6 — блокуючі. Решта — контрольні.

## Побічний результат для подальшої перевірки

95 зі 171 локалізованого рядка мають англомовний оригінал у цьому ж датасеті. Після групування по `course_title_base` видно розбіжності в обсязі між версіями одного курсу:

| `course_slug` | `language` | `total_minutes` |
|---|---|---|
| `gcp-fundamentals` | en | 212,2 |
| `gcp-fundamentals-jp` | ja | 211,3 |
| `gcp-fundamentals-es` | es | 211,3 |
| `gcp-fundamentals-fr` | fr | **231,3** |
| `gcp-fundamentals-core-infrastructure-id` | id | **213,3** |
| `gcp-fundamentals-ko` | ko | 211,3 |

Це не входить у поточні правила — передати на окрему перевірку разом із дефектом подвоєння в `C00169` (той самий slug `gcp-fundamentals`, значення рівно вдвічі більші за пʼять інших рядків).

---

# Послідовність виконання

```
Крок 1 — R3.1   Обрізання пробілів
Крок 2 — R1     Додавання course_slug
Крок 3 — R2     Маскований NULL -> NULL
Крок 4 — R4     Мова і базова назва
Крок 5 — R3.2   Зміна типів
```

## Критичні залежності

**Залежність 1: R3.1 перед R4.**
Маркери мови стоять у кінці рядка. Хвостовий пробіл не дасть розпізнати маркер — 122 рядки з пробілами зіпсують визначення мови.

**Залежність 2: у межах R2 `dim_specialization` перед `dim_course`.**
Якщо почистити `Unknown` тільки в `dim_course`, звірка між таблицями (V2.6) почне падати при кожному завантаженні.

**Рекомендація щодо R3.2.**
Зміна типів виконується останньою. Будь-який `UPDATE` після неї потребуватиме приведення типів і може впасти.

# Підсумок змін у `dim_course`

| Категорія | Кількість | Колонки |
|---|---|---|
| Додано | 3 | `course_slug`, `language`, `course_title_base` |
| Змінено тип | 2 | `n_weeks`, `practice_min` |
| Змінено значення | 4 | `course` (обрізання), `n_reviews` (0 → NULL), `level` і `domain` (через перезалив) |
| Видалено | 0 | — |

Після виконання: **3 032 рядки × 24 колонки**.

# Зведена таблиця перевірок

| ID | Правило | Що перевіряє | Очікується | Критичність |
|---|---|---|---|---|
| V1.1 | R1 | Повнота `course_slug` | 0 | блокуюча |
| V1.2 | R1 | Набір символів | 0 | блокуюча |
| V1.3 | R1 | Кардинальність | 2 917 | блокуюча |
| V1.4 | R1 | Довжина | 2 … 80 | попереджувальна |
| V1.5 | R1 | Один slug → одна назва | 0 | попереджувальна |
| V2.1 | R2 | Маркери прибрані | 0 | блокуюча |
| V2.2 | R2 | NULL стали видимими | 196 / 137 | контрольна |
| V2.3 | R2 | Інваріант `n_reviews` ↔ `mean_stars` | 0 | блокуюча |
| V2.4 | R2 | Непорожніх відгуків | 254 | контрольна |
| V2.5 | R2 | Середня кількість відгуків | ≈624 | контрольна |
| V2.6 | R2 | Звірка копій із джерелом | 0 | блокуюча |
| V3.1.1 | R3.1 | Пробілів немає | 0 | блокуюча |
| V3.1.2 | R3.1 | Порожніх назв немає | 0 | блокуюча |
| V3.1.3 | R3.1 | Кількість рядків | 3 032 | блокуюча |
| V3.2.1 | R3.2 | Типи застосовані | smallint / integer | блокуюча |
| V3.2.2 | R3.2 | Медіани не спотворені | 4 / 135 | контрольна |
| V3.2.3 | R3.2 | Ведучі нулі | `C00000` / `S0000` | блокуюча |
| V3.2.4 | R3.2 | Непорожніх значень | 3 019 | контрольна |
| V4.1 | R4 | Розподіл мов | 8 категорій | контрольна |
| V4.2 | R4 | Базова назва заповнена | 0 | блокуюча |
| V4.3 | R4 | `en` = оригінал | 0 | блокуюча |
| V4.4 | R4 | Не-`en` коротша | 0 | контрольна |
| V4.5 | R4 | Звірка зі slug | 171 | контрольна |
| V4.6 | R4 | Маркерів не лишилось | 0 | блокуюча |
| V4.7 | R4 | Локалізованих | 171 / 151 | контрольна |

**Блокуючих перевірок: 13.** Будь-яка з них, що не пройшла, зупиняє конвеєр.
