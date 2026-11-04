"""Starter content for Interview Prep and Skill Assessments (admins can edit/add/delete from the panel)."""
from app.database import assessments_collection, prep_items_collection
from app.utils import now

PREP = [
    # (category, difficulty, question, answer)
    ("HR & Behavioral", "easy", "Tell me about yourself.", "Give a 60-90 second summary: current role, 2-3 relevant achievements, and why you are excited about this role. Use present -> past -> future. Avoid reciting your whole resume."),
    ("HR & Behavioral", "easy", "Why do you want to work here?", "Mention something specific you learned about the company (product, mission, team), connect it to your skills, and say what you want to learn or contribute. Generic answers like 'good brand' stand out for the wrong reason."),
    ("HR & Behavioral", "medium", "Describe a time you handled a conflict in a team.", "Use the STAR method: Situation, Task, Action, Result. Focus on listening, finding the shared goal and the outcome. Never blame a person; describe the problem and what you did."),
    ("HR & Behavioral", "medium", "What is your biggest weakness?", "Pick a real but non-critical weakness, show the steps you are taking to improve (a course, a habit, feedback) and a small proof of progress."),
    ("HR & Behavioral", "easy", "Where do you see yourself in five years?", "Show ambition that fits the company: growing your skills, taking on more ownership, maybe mentoring. Do not say you plan to leave or that you have no plan."),
    ("HR & Behavioral", "medium", "Why are you leaving your current job?", "Stay positive and forward-looking: seeking growth, new challenges or a better fit. Never criticise your current employer."),
    ("Python", "easy", "What is the difference between a list and a tuple?", "Lists are mutable (can change after creation) and use [] ; tuples are immutable and use (). Tuples are hashable when their items are, so they can be dict keys or set members, and they are slightly faster and signal 'fixed data'."),
    ("Python", "medium", "What are decorators in Python?", "A decorator is a function that takes a function and returns a new function, adding behaviour without changing the original code (logging, caching, auth checks). Written with @name above the function definition; use functools.wraps to keep metadata."),
    ("Python", "medium", "Explain the GIL.", "The Global Interpreter Lock in CPython lets only one thread execute Python bytecode at a time. CPU-bound work needs multiprocessing (or native extensions); I/O-bound work still benefits from threads or asyncio."),
    ("Python", "easy", "What is the difference between `is` and `==`?", "`==` compares values (equality); `is` compares identity (same object in memory). Use `is` for None checks: `x is None`."),
    ("Python", "hard", "How does Python manage memory?", "Reference counting frees objects when the count drops to zero, plus a cyclic garbage collector for reference cycles. Objects live in a private heap managed by the interpreter."),
    ("JavaScript", "easy", "What is the difference between `let`, `const` and `var`?", "`var` is function-scoped and hoisted (initialised as undefined). `let` and `const` are block-scoped and live in the temporal dead zone until declared. `const` cannot be re-assigned (objects it points to can still change)."),
    ("JavaScript", "medium", "Explain closures.", "A closure is a function that remembers the variables of the scope where it was created, even after that scope has finished. Used for private state, factories and callbacks."),
    ("JavaScript", "medium", "What is the event loop?", "JavaScript runs on one thread. Async work (timers, network) is handled by the environment and its callbacks are queued; the event loop runs them when the call stack is empty. Promise callbacks (microtasks) run before timer callbacks (macrotasks)."),
    ("JavaScript", "easy", "What is the difference between `==` and `===`?", "`==` converts types before comparing (0 == '0' is true); `===` compares value and type without conversion (0 === '0' is false). Prefer `===`."),
    ("JavaScript", "hard", "What is the difference between `Promise.all` and `Promise.allSettled`?", "`Promise.all` rejects as soon as one promise rejects; `Promise.allSettled` waits for all and returns each result as fulfilled/rejected, so you can handle partial failures."),
    ("React", "easy", "What is the virtual DOM?", "An in-memory representation of the UI. On state change React builds a new tree, diffs it against the previous one and applies only the minimal real DOM updates."),
    ("React", "medium", "When does `useEffect` run, and how do you clean up?", "After render, and again when a dependency in its array changes ([] = once after mount). Return a function from the effect to clean up (remove listeners, cancel timers/requests) before the next run or unmount."),
    ("React", "medium", "Why do list items need a `key`?", "Keys let React match items between renders so it can reorder/update the right DOM nodes and keep component state. Use a stable unique id, not the array index when the list can change."),
    ("React", "hard", "Controlled vs uncontrolled components?", "Controlled inputs get their value from React state and update through onChange (single source of truth). Uncontrolled inputs keep their own DOM state and are read through refs."),
    ("SQL & Databases", "easy", "What is the difference between INNER JOIN and LEFT JOIN?", "INNER JOIN returns only rows that match in both tables. LEFT JOIN returns all rows from the left table and matching rows from the right (NULLs where there is no match)."),
    ("SQL & Databases", "medium", "What is an index and what is the trade-off?", "A data structure (usually a B-tree) that speeds up lookups and sorting on a column. The cost is extra storage and slower writes because the index must be updated."),
    ("SQL & Databases", "medium", "WHERE vs HAVING?", "WHERE filters rows before grouping; HAVING filters groups after aggregation (e.g. HAVING COUNT(*) > 5)."),
    ("SQL & Databases", "medium", "SQL vs NoSQL (MongoDB)?", "SQL: fixed schema, joins, strong ACID guarantees - good for relational data. MongoDB: flexible documents, horizontal scaling, fast development - good for varied or nested data. Choose by access patterns and consistency needs."),
    ("Web & APIs", "easy", "What are the common HTTP methods and what do they mean?", "GET reads, POST creates, PUT replaces, PATCH partially updates, DELETE removes. GET/PUT/DELETE are idempotent; POST is not."),
    ("Web & APIs", "medium", "What is CORS?", "A browser security rule that blocks a page from reading responses from another origin unless the server allows it with Access-Control-Allow-Origin headers. It protects users; it is not an authentication mechanism."),
    ("Web & APIs", "medium", "How does JWT authentication work?", "After login the server signs a token containing the user id and expiry. The client sends it in the Authorization header; the server verifies the signature and expiry on each request. Keep secrets safe, use short expiry and HTTPS."),
    ("Web & APIs", "hard", "How would you prevent common web vulnerabilities (XSS, SQL/NoSQL injection, CSRF)?", "Escape/encode output (frameworks like React do), validate and type-check input, use parameterised queries/ORM, never concatenate user input into queries, use SameSite cookies or tokens for CSRF, set security headers and keep dependencies updated."),
    ("System Design Basics", "medium", "How would you design a URL shortener?", "Generate a unique short code (base62 of an auto-increment id or random), store code -> long URL in a key-value/DB, redirect with 301/302, add cache for hot links, rate-limit creation and track clicks asynchronously."),
    ("System Design Basics", "hard", "What is caching and when does it hurt?", "Storing computed/fetched data closer to the reader to cut latency and load. It hurts when data goes stale, when invalidation is wrong, or when the cache hides slow queries that should be fixed."),
]

QUIZZES = [
    {
        "title": "Python Fundamentals", "skill": "Python", "description": "Core syntax, data types and common built-ins.", "pass_percent": 60,
        "questions": [
            ("What does `len([1, 2, [3, 4]])` return?", ["2", "3", "4", "Error"], 1, "The list has three elements; the inner list counts as one."),
            ("Which of these is immutable?", ["list", "dict", "set", "tuple"], 3, "Tuples cannot be changed after creation."),
            ("What is the output of `print(type(3 / 2))`?", ["<class 'int'>", "<class 'float'>", "<class 'double'>", "<class 'number'>"], 1, "`/` always returns a float in Python 3."),
            ("Which keyword defines a function?", ["func", "function", "def", "lambda only"], 2, "Functions are defined with `def`."),
            ("What does `'abc'[::-1]` give?", ["'abc'", "'cba'", "'bca'", "Error"], 1, "A step of -1 reverses the string."),
            ("How do you handle an exception?", ["try / except", "catch / throw", "try / catch", "handle / error"], 0, "Python uses try/except."),
            ("What is `bool([])`?", ["True", "False", "None", "Error"], 1, "Empty containers are falsy."),
            ("Which statement creates a dictionary?", ["d = [1: 2]", "d = {1: 2}", "d = (1: 2)", "d = <1, 2>"], 1, "Curly braces with key: value pairs."),
        ],
    },
    {
        "title": "JavaScript Fundamentals", "skill": "JavaScript", "description": "Types, scope, functions and the async basics.", "pass_percent": 60,
        "questions": [
            ("What does `typeof null` return?", ["'null'", "'object'", "'undefined'", "'number'"], 1, "A historic quirk: typeof null is 'object'."),
            ("Which declaration is block scoped and cannot be re-assigned?", ["var", "let", "const", "function"], 2, "`const` is block scoped and cannot be re-assigned."),
            ("What is `[1, 2, 3].map(x => x * 2)`?", ["[1, 2, 3]", "[2, 4, 6]", "6", "undefined"], 1, "map returns a new array with each item transformed."),
            ("What does `'5' + 3` evaluate to?", ["8", "'53'", "NaN", "Error"], 1, "With a string operand, + concatenates."),
            ("Which method adds an item to the end of an array?", ["push", "pop", "shift", "unshift"], 0, "`push` appends; `pop` removes the last item."),
            ("What does `await` do?", ["Blocks the whole thread", "Pauses the async function until the promise settles", "Creates a new thread", "Cancels a promise"], 1, "It suspends only the async function, not the thread."),
            ("What is the result of `0.1 + 0.2 === 0.3`?", ["true", "false", "NaN", "Error"], 1, "Floating point rounding makes it false."),
            ("Which creates a copy of an object (shallow)?", ["const b = a", "const b = {...a}", "const b = a.copy()", "const b = &a"], 1, "Spread creates a shallow copy."),
        ],
    },
    {
        "title": "SQL Basics", "skill": "SQL", "description": "Queries, filtering, joins and aggregation.", "pass_percent": 60,
        "questions": [
            ("Which clause filters rows before grouping?", ["HAVING", "WHERE", "GROUP BY", "ORDER BY"], 1, "WHERE filters rows; HAVING filters groups."),
            ("Which statement removes all rows but keeps the table?", ["DROP TABLE", "DELETE FROM table (no WHERE)", "REMOVE table", "CLEAR table"], 1, "DELETE without WHERE (or TRUNCATE) empties the table."),
            ("What does `COUNT(*)` return?", ["Number of distinct values", "Number of rows", "Sum of values", "Number of columns"], 1, "COUNT(*) counts rows."),
            ("Which join keeps all rows from the left table?", ["INNER JOIN", "LEFT JOIN", "CROSS JOIN", "SELF JOIN only"], 1, "LEFT JOIN keeps every left row."),
            ("Which keyword removes duplicate rows from results?", ["UNIQUE", "DISTINCT", "SINGLE", "DIFFERENT"], 1, "SELECT DISTINCT removes duplicates."),
            ("Which is used to sort results descending?", ["ORDER BY col DESC", "SORT col DOWN", "GROUP BY col DESC", "ORDER col REVERSE"], 0, "ORDER BY ... DESC."),
            ("A PRIMARY KEY column must be...", ["Unique and not NULL", "Only numeric", "Indexed twice", "Unique but may be NULL"], 0, "Primary keys are unique and non-null."),
            ("Which function returns the highest value?", ["TOP()", "HIGH()", "MAX()", "BIGGEST()"], 2, "MAX() returns the maximum."),
        ],
    },
    {
        "title": "Logical Reasoning", "skill": "Aptitude", "description": "Short aptitude questions often used in screening rounds.", "pass_percent": 60,
        "questions": [
            ("What comes next: 2, 4, 8, 16, ?", ["24", "30", "32", "34"], 2, "Each number doubles."),
            ("If all Bloops are Razzies and all Razzies are Lazzies, then all Bloops are...", ["Lazzies", "Not Lazzies", "Only some Lazzies", "Cannot say"], 0, "Transitive: Bloops -> Razzies -> Lazzies."),
            ("A train travels 120 km in 2 hours. Its speed is...", ["40 km/h", "50 km/h", "60 km/h", "70 km/h"], 2, "120 / 2 = 60."),
            ("What is 15% of 200?", ["20", "25", "30", "35"], 2, "0.15 x 200 = 30."),
            ("Find the odd one out: 3, 5, 7, 9, 11", ["3", "5", "9", "11"], 2, "9 is not prime; the others are."),
            ("If a pen costs 12 and a notebook costs 3 times as much, 2 notebooks cost...", ["36", "48", "60", "72"], 3, "Notebook = 36, so two = 72."),
            ("What comes next: A, C, F, J, ?", ["M", "N", "O", "P"], 2, "Gaps grow by one: +2, +3, +4, +5 -> O."),
            ("Five people finish a job in 10 days. How many days will 10 people take?", ["2", "5", "10", "20"], 1, "Double the people, half the days."),
        ],
    },
]


def seed_learning() -> None:
    if prep_items_collection.count_documents({}) == 0:
        ts = now()
        prep_items_collection.insert_many(
            [{"category": c, "difficulty": d, "question": q, "answer": a, "is_active": True, "created_at": ts, "updated_at": ts} for c, d, q, a in PREP]
        )
    if assessments_collection.count_documents({}) == 0:
        ts = now()
        assessments_collection.insert_many(
            [
                {
                    "title": q["title"], "skill": q["skill"], "description": q["description"], "pass_percent": q["pass_percent"], "is_active": True,
                    "questions": [{"q": t, "options": o, "answer": a, "explanation": e} for t, o, a, e in q["questions"]],
                    "created_at": ts, "updated_at": ts,
                }
                for q in QUIZZES
            ]
        )
