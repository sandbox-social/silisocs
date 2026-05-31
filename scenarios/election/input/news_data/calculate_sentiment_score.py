import matplotlib.pyplot as plt
import numpy as np
import requests
from news_agent_utils import get_candidate_headlines, load_json

API_URL = (
    "https://api-inference.huggingface.co/models/cardiffnlp/twitter-xlm-roberta-base-sentiment"
)
headers = {"Authorization": "Bearer "}


def query(payload):
    response = requests.post(API_URL, headers=headers, json=payload)
    return response.json()


def get_candidate_headline_sentiments(candidate_headlines):
    sentiments = []
    for headline in candidate_headlines:
        payload = {"inputs": headline}
        sentiments.append(query(payload))
    return sentiments


def calc_log_ratio(sentiment):
    pos, neg, neutral = 0, 0, 0
    # seems inefficient but doing it to be sure of the order of the sentiments
    for s in sentiment:
        if s["label"] == "positive":
            pos += s["score"]
        elif s["label"] == "negative":
            neg += s["score"]
        else:
            neutral += s["score"]
    # normalize
    pos = pos / sum([pos, neg, neutral])
    neg = neg / sum([pos, neg, neutral])
    neutral = neutral / sum([pos, neg, neutral])
    # calculate log ratio
    log_ratio = np.log(pos / neg)
    return log_ratio


def calc_avg_log_ratio(sentiments):
    log_ratios = []
    for s in sentiments:
        log_ratios.append(calc_log_ratio(s[0]))
    return np.mean(log_ratios), np.std(log_ratios) / np.sqrt(len(log_ratios))


def main(headlines):
    bill_headlines, bradley_headlines, other_headlines = get_candidate_headlines(headlines)
    bill_sentiments = get_candidate_headline_sentiments(bill_headlines)
    bradley_sentiments = get_candidate_headline_sentiments(bradley_headlines)
    avg_bill_log_ratio, se_bill_log_ratio = calc_avg_log_ratio(bill_sentiments)
    avg_bradley_log_ratio, se_bradley_log_ratio = calc_avg_log_ratio(bradley_sentiments)

    return {
        "bill": {"avg_log_ratio": avg_bill_log_ratio, "se_log_ratio": se_bill_log_ratio},
        "bradley": {"avg_log_ratio": avg_bradley_log_ratio, "se_log_ratio": se_bradley_log_ratio},
    }


def plot_bias_scores(neutral_sentiments, bill_bias_sentiments, bradley_bias_sentiments):
    conditions = ["Neutral\nNews", "Biased \n(pro-Bill)\n News", "Biased\n(pro-Bradley)\nNews"]
    bill_ratios = [
        neutral_sentiments["bill"]["avg_log_ratio"],
        bill_bias_sentiments["bill"]["avg_log_ratio"],
        bradley_bias_sentiments["bill"]["avg_log_ratio"],
    ]
    bill_se = [
        neutral_sentiments["bill"]["se_log_ratio"],
        bill_bias_sentiments["bill"]["se_log_ratio"],
        bradley_bias_sentiments["bill"]["se_log_ratio"],
    ]

    bradley_ratios = [
        neutral_sentiments["bradley"]["avg_log_ratio"],
        bill_bias_sentiments["bradley"]["avg_log_ratio"],
        bradley_bias_sentiments["bradley"]["avg_log_ratio"],
    ]
    bradley_se = [
        neutral_sentiments["bradley"]["se_log_ratio"],
        bill_bias_sentiments["bradley"]["se_log_ratio"],
        bradley_bias_sentiments["bradley"]["se_log_ratio"],
    ]

    # Set up the plot
    fig, ax = plt.subplots(figsize=(3.5, 2))

    # Set the width of each bar and the positions of the bars
    width = 0.35
    x = np.arange(len(conditions))

    # Create the bars
    rects1 = ax.bar(
        x - width / 2,
        bill_ratios,
        width,
        label="Bias for Bill",
        color="#E74C3C",
        yerr=bill_se,
        capsize=3,
    )
    rects2 = ax.bar(
        x + width / 2,
        bradley_ratios,
        width,
        label="Bias for\nBradley",
        color="#2E86C1",
        yerr=bradley_se,
        capsize=3,
    )

    # Customize the plot
    fontsize = 11
    ax.set_ylabel("Average Bias", fontsize=fontsize)  # \n\n $(Log(P(Positive)/P(Negative)))$')
    ax.set_title("Sentiment Bias vs. News Type", fontsize=fontsize)
    ax.set_xticks(x)
    ax.set_xticklabels(conditions, fontsize=fontsize)
    ax.legend()
    plt.grid(True, alpha=0.5)
    plt.savefig("./bias_plot.png", bbox_inches="tight")


if __name__ == "__main__":
    neutral_headlines = load_json("./v1_news_headlines_no_bias.json")
    neutral_sentiments = main(neutral_headlines)

    bill_bias_headlines = load_json("./v1_news_headlines_bill_bias.json")
    bill_bias_sentiments = main(bill_bias_headlines)

    bradley_bias_headlines = load_json("./v1_news_headlines_bradley_bias.json")
    bradley_bias_sentiments = main(bradley_bias_headlines)

    plot_bias_scores(neutral_sentiments, bill_bias_sentiments, bradley_bias_sentiments)
