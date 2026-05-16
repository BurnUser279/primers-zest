import os
import jinja2

template_dir = 'templates'
env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))

errors = []
for filename in os.listdir(template_dir):
    if filename.endswith('.html'):
        try:
            with open(os.path.join(template_dir, filename), 'r', encoding='utf-8') as f:
                template_source = f.read()
            env.parse(template_source)
            print(f"OK: {filename}")
        except Exception as e:
            print(f"ERROR in {filename}: {e}")
            errors.append((filename, e))

if not errors:
    print("\nNo Jinja2 syntax errors found.")
else:
    print(f"\nFound {len(errors)} errors.")
