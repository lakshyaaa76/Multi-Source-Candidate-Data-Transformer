"""
One-off script (Phase 2) to generate synthetic resume PDF fixtures with reportlab.
Not part of the pipeline itself — run once to produce samples/*.pdf.
"""
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch


def make_text_resume(path: str, lines: list[str]) -> None:
    c = canvas.Canvas(path, pagesize=LETTER)
    width, height = LETTER
    text = c.beginText(0.75 * inch, height - 0.75 * inch)
    text.setFont("Helvetica", 11)
    for line in lines:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()


def make_blank_image_pdf(path: str) -> None:
    """A PDF with no extractable text layer at all — simulates a scanned resume."""
    c = canvas.Canvas(path, pagesize=LETTER)
    width, height = LETTER
    # Draw shapes only, no text/font drawing calls -> pdfplumber extracts "" text.
    c.setFillGray(0.85)
    c.rect(1 * inch, 1 * inch, width - 2 * inch, height - 2 * inch, fill=1, stroke=0)
    c.setFillGray(0.6)
    c.rect(1.5 * inch, height - 2 * inch, 2 * inch, 0.6 * inch, fill=1, stroke=0)
    c.showPage()
    c.save()


jane_doe = [
    "Jane Doe",
    "jane.doe@example.com | +1 (415) 555-0142 | San Francisco, CA, USA",
    "linkedin.com/in/janedoe | github.com/janedoe",
    "",
    "HEADLINE",
    "Senior Backend Engineer focused on distributed systems and developer tooling",
    "",
    "EXPERIENCE",
    "Initech -- Senior Backend Engineer",
    "Jan 2022 - Present",
    "Leading the payments infrastructure team; migrated core services to Go.",
    "",
    "Acme Corp -- Backend Engineer",
    "Jun 2019 - Dec 2021",
    "Built internal tooling for the data platform team using Python and Postgres.",
    "",
    "EDUCATION",
    "University of Washington -- B.S. Computer Science, 2019",
    "",
    "SKILLS",
    "Python, Go, AWS, PostgreSQL, Docker, Kubernetes",
]

john_smith = [
    "John Smith",
    "john.smith@example.com | 212-555-0199 | New York, NY, USA",
    "",
    "HEADLINE",
    "Product leader with 8+ years shipping consumer fintech products",
    "",
    "EXPERIENCE",
    # Deliberately a THIRD different employer name vs CSV ("Globex Corp") and
    # ATS ("Soylent Corp"), to exercise a three-way scalar conflict in merge.
    "Globex Corp (recently rebranded internally to 'Globex Labs') -- Group Product Manager",
    "Mar 2023 - Present",
    "Own the core checkout experience across web and mobile.",
    "",
    "Initrode -- Product Manager",
    "2018 - 2023",
    "Launched three major product lines from 0 to 1.",
    "",
    "EDUCATION",
    "NYU Stern -- MBA, 2018",
    "",
    "SKILLS",
    "Roadmapping, SQL, A/B Testing, Stakeholder Management",
]

# Alice Nguyen exists ONLY in unstructured sources (resume + notes) -- no CSV/ATS row --
# to exercise "candidate with no structured source at all" (PROJECT_CONTEXT.md §16).
alice_nguyen = [
    "Alice Nguyen",
    "alice.nguyen.dev@example.com | +1 503 555 0177 | Portland, OR, USA",
    "github.com/anguyen-dev",
    "",
    "HEADLINE",
    "Full-stack engineer, recently focused on accessibility tooling",
    "",
    "EXPERIENCE",
    "Bright Path Software -- Full-Stack Engineer",
    "Aug 2021 - Present",
    "Built an internal design-system + accessibility linting toolchain.",
    "",
    "EDUCATION",
    "Portland State University -- B.S. Computer Science, 2021",
    "",
    "SKILLS",
    "TypeScript, React, Node.js, GraphQL, Accessibility (WCAG)",
]

if __name__ == "__main__":
    make_text_resume("samples/resume_jane_doe.pdf", jane_doe)
    make_text_resume("samples/resume_john_smith.pdf", john_smith)
    make_text_resume("samples/resume_alice_nguyen.pdf", alice_nguyen)
    make_blank_image_pdf("samples/resume_scanned_no_text.pdf")
    print("Generated 4 PDFs in samples/")
