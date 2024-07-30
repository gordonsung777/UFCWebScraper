from flask import Flask, request, jsonify, send_file, render_template
import os
from bs4 import BeautifulSoup
import requests
import pandas as pd
import re
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from celery import Celery
from flask_cors import CORS
import matplotlib

matplotlib.use('Agg')  # Use Agg backend to avoid GUI-related issues
import matplotlib.pyplot as plt


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'pdfs'

# Celery Configuration
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

# Create Celery instance
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# Add CORS headers to allow cross-origin requests
CORS(app)

# Create the 'pdfs' folder if it doesn't exist
if not os.path.exists('pdfs'):
    os.makedirs('pdfs')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/scrape', methods=['GET'])
def scrape():
    url = "https://www.ufc.com/athletes/all?gender=All&search=&page="

    not_empty = True
    end_page = 5  # For demonstration, limit to a few pages
    start_page = 1

    scraped_data = []

    while not_empty and start_page <= end_page:
        url1 = url + str(start_page)
        page = requests.get(url1)

        soup = BeautifulSoup(page.content, 'html.parser')

        lists = soup.find_all('div', class_="c-listing-athlete__text")
        if len(lists) > 0:
            for athlete_card in lists:
                nickname = ""
                fullname = ""
                weight = ""
                record = ""
                totalwins = ""
                totalloss = ""
                totaldraws = ""
                totalfights = ""

                athlete_name = athlete_card.find('span', class_="c-listing-athlete__nickname")
                if athlete_name is not None:
                    nickname = athlete_name.text.strip()
                athlete_fullname = athlete_card.find('span', class_="c-listing-athlete__name")
                if athlete_fullname is not None:
                    fullname = athlete_fullname.text.strip()
                athlete_weight = athlete_card.find('span', class_="c-listing-athlete__title")
                if athlete_weight is not None:
                    weight = athlete_weight.text.strip()
                athlete_record = athlete_card.find('span', class_="c-listing-athlete__record")
                if athlete_record is not None:
                    record = athlete_record.text.strip()

                values = re.findall(r'\d+|[WDL]', record)
                vdict = dict(zip(values[3:], values[:3]))
                totalwins = vdict["W"]
                totalloss = vdict["L"]
                totaldraws = vdict["D"]
                totalfights = str(int(totalwins) + int(totalloss) + int(totaldraws))

                scraped_data.append({
                    'Nickname': nickname,
                    'Fullname': fullname,
                    'Weight': weight,
                    'Record': record,
                    'totalwins': totalwins,
                    'totalloss': totalloss,
                    'totaldraws': totaldraws,
                    'totalfights': totalfights
                })
            start_page += 1
        else:
            not_empty = False

    return jsonify(scraped_data)

@app.route('/generate-pdf', methods=['POST'])
def generate_pdf():
    scraped_data = request.json

    if scraped_data:
        pdf_filename = os.path.join(app.config['UPLOAD_FOLDER'], 'pic.pdf')
        pdf_pages = PdfPages(pdf_filename)

        for row in scraped_data:
            fig, ax = plt.subplots(1, 1)
            fig.set_size_inches(5, 5)

            total_wins = int(row['totalwins'])
            total_losses = int(row['totalloss'])
            total_draws = int(row['totaldraws'])

            if total_wins == 0 and total_losses == 0 and total_draws == 0:
                # Handle the case where all values are zero
                continue

            wedges, texts, autotexts = ax.pie([total_wins, total_losses, total_draws],
                                            labels=["Total Wins", "Total Losses", "Total Draws"],
                                            autopct='%1.1f%%', shadow=True,
                                            explode=(0.1, 0, 0))
            ax.axis('equal')

            ax.set_title(row['Fullname'])
            ax.set_ylabel("Total Wins: " + str(total_wins) + ", Total Losses: " + str(total_losses) + ", Total Draws: " + str(total_draws))

            pdf_pages.savefig(fig)



        pdf_pages.close()

        return pdf_filename
    else:
        return "Scraped data is missing", 400

@app.route('/download-pdf')
def download_pdf():
    pdf_filename = os.path.join(app.config['UPLOAD_FOLDER'], 'pic.pdf')
    return send_file(pdf_filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
