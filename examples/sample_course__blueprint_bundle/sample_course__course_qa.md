# Course QA Report — sample_course

- Breaks: 0
- Warnings: 2
- Notes: 4
- Scope: dropbox_folders: 4, discussion_topics: 3, quizzes: 1, grade_items: 8, manifest_items: 25, html_topics: 14, external_urls: 0

Read-only diagnostics over the export. Breaks need a decision before
launch/import; warnings deserve a look; notes are context. External
URLs are inventoried, not fetched.

## Breaks

- None.

## Warnings

- grade item with no linked activity: 'Standalone Participation'
- images missing alt text across activities and pages: 1

## Notes

- content file 'Case Packet PDF': body extraction skipped for non-HTML file: files/week1-case-packet.pdf
- hidden content 'Instructor Notes Draft': body extraction skipped: files/hidden-instructor-notes.docx
- hidden content 'Hidden Faculty Setup Page': body extraction skipped: pages/hidden-faculty-setup.html
- gradebook countable total: 73 (set expected_total in --config to enforce)

## External URLs (inventory only)

- None found.
