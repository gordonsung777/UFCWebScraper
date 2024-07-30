from celery import Celery

app = Celery('myapp', broker='redis://localhost:6379/0')

@app.task
def generate_pdf_task(scraped_data):
    # Your PDF generation code here
    return "PDF generation completed"
