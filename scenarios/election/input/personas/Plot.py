import json
import textwrap

import pandas as pd
import plotly.express as px
from sentence_transformers import SentenceTransformer
from sklearn.decomposition import PCA


def infer_political_leaning(context):
    context_lower = context.lower()
    if "conservative" in context_lower:
        return "Conservative"
    if "liberal" in context_lower:
        return "Liberal"
    return "Other"


json_file = "genderless_context.json"
with open(json_file) as f:
    data = json.load(f)

contexts = []
names = []
leanings = []
wrapped_contexts = []

for item in data:
    context = item.get("context", "")
    name = item.get("Name", "Unknown")
    leaning = infer_political_leaning(context)

    contexts.append(context)
    names.append(name)
    leanings.append(leaning)

    wrapped_context = textwrap.fill(context, width=50).replace("\n", "<br>")
    wrapped_contexts.append(wrapped_context)


model = SentenceTransformer("all-MiniLM-L6-v2")
embeddings = model.encode(contexts, show_progress_bar=True)


pca = PCA(n_components=2)
embeddings_2d = pca.fit_transform(embeddings)


df = pd.DataFrame(
    {
        "1st PCA Component": embeddings_2d[:, 0],
        "2nd PCA Component": embeddings_2d[:, 1],
        "Name": names,
        "Context": wrapped_contexts,
        "Political Leaning": leanings,
    }
)


tick_labels = [-0.4, -0.2, 0, 0.2, 0.4]


fig = px.scatter(
    df,
    x="1st PCA Component",
    y="2nd PCA Component",
    color="Political Leaning",
    color_discrete_map={"Conservative": "red", "Liberal": "blue", "Other": "grey"},
    hover_data={
        "Name": True,
        "Context": True,
        "Political Leaning": False,
        "1st PCA Component": False,
        "2nd PCA Component": False,
    },
    title="Embeddings of Posts Summary",
)


fig.update_traces(marker=dict(size=12, line=dict(width=1, color="DarkSlateGrey")))


fig.update_layout(
    font=dict(size=36),
    title_font=dict(size=48),
    xaxis_title="1st PCA component",
    yaxis_title="2nd PCA component",
    xaxis=dict(
        tickmode="array", tickvals=tick_labels, ticktext=[str(tick) for tick in tick_labels]
    ),
    yaxis=dict(
        tickmode="array", tickvals=tick_labels, ticktext=[str(tick) for tick in tick_labels]
    ),
    legend_title_text="",
    legend=dict(
        orientation="h",
        x=0.5,
        y=1.05,
        xanchor="center",
        yanchor="bottom",
        bordercolor="black",
        borderwidth=1,
        font=dict(size=48),
    ),
    margin=dict(l=50, r=50, t=250, b=50),
    title_x=0.5,
    autosize=False,
    width=1000,
    height=900,
)

fig.write_image("embeddings.png", scale=2)


fig.show()
