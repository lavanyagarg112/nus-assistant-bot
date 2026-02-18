# TODO

## Completed
- [x] Initialize git repo
- [x] Error handling / logging improvements (global error handler)
- [x] Include quizzes in /due and /assignments views
- [x] Browse course files (/files with folder navigation)
- [x] Show course name alongside assignment notes in /notes
- [x] Add direct Canvas links to assignments, quizzes, etc.
- [x] Todo system per course (replaced course reminders with /todos + /add_todo)

## Future Ideas
- [ ] Course caching — cache course name → id mapping in the DB (auto-fetched and stored on first use, re-fetched periodically or on demand). Avoids repeated API calls for course name lookups throughout the app.
- [ ] Notes management improvements — filtering by course, date ranges, search, bulk operations
- [ ] Add reminders to individual todos (notify X days/hours before)
- [ ] Make reminders more flexible — recurring, custom intervals, per-todo reminders, etc.
- [ ] Command to view completed todos
