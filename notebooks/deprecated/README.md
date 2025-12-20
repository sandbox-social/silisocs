# Getting started with the Concordia simulation environment

> [!NOTE]
> If you have any questions or issues while working through these examples, please don't hesitate to reach out. Your feedback and queries are valuable in improving this guide!

## Prerequisites

Follow the [installation steps](https://github.com/social-sandbox/mastodon-sim?tab=readme-ov-file#development-installation) in order to be able to run these notebook examples.

## Core Examples

**The suggested order for going through the notebook examples is as follows:**

### 1. Read through the [`Calendar.ipynb`](https://github.com/social-sandbox/mastodon-sim/blob/main/notebooks/Calendar.ipynb) example 

Before running this example, read it end-to-end without clearing the notebook's current outputs.

- Focus on the "Analyzing this episode" markdown section in the notebook carefully, and ensure understanding of how this procedure produced the run's output. Click through to the code examples to see the prompts and how things are actually executed in Concordia.
- Read the implementation details for the mainline repo's versions of `triggering.py`, `scene.py`, and `app.py` in full to understand how these work in more detail. [Link](https://github.com/google-deepmind/concordia/tree/2684366df2e70993bccb2ea3630cbe7cc7d91a7a/examples/phone/components)

### 2. Run the `Calendar.ipynb` example

After reading the example end-to-end with the current outputs, clear the notebook and try running it yourself end-to-end. Begin to notice in what ways the output may change (good or bad). Start to think about shortcomings of this example and how they might be improved

### 3. Read through the [`Concordia+Mastodon.ipynb`](https://github.com/social-sandbox/mastodon-sim/blob/main/notebooks/Concordia%2BMastodon.ipynb)

Again, first read this example end-to-end without clearing the prior outputs.

Note that `USE_MASTODON_SERVER` has been set to `False` in this notebook, so the code will not actually execute any Mastodon API calls.

Observe how the following code has been updated:

- Additional player components have been added
- This example uses a custom `CALL_TO_ACTION` for the main GM
- This example uses custom thought chains for the main GM (`CUSTOM_THOUGHTS`)
- Several updates have been made to the `triggering.py`, `scene.py`, and `app.py` modules
  - Compare [these new versions](https://github.com/social-sandbox/mastodon-sim/tree/main/src/mastodon_sim/concordia/components) to the prior versions in step 1

### 4. Run the `Concordia+Mastodon.ipynb` example

After reading the example end-to-end with the current outputs, clear the notebook and try running it yourself end-to-end. Similarly, take note of any changes in the output.

## Optional Examples: executing real Mastodon API calls

### 5. Run the [`Mastodon.ipynb`](https://github.com/social-sandbox/mastodon-sim/blob/main/notebooks/Mastodon.ipynb) example

1. Installation:

   [https://github.com/social-sandbox/mastodon-sim?tab=readme-ov-file#development-installation](https://github.com/social-sandbox/mastodon-sim?tab=readme-ov-file#development-installation)

2. Next, create a file named `.env` in the root directory of the repo, and add this to its contents (filling in the required information):

   ```txt
   # Mastodon API base URL
   API_BASE_URL=https://social-sandbox.com

   # Email prefix for user accounts
   # Each user needs a unique email address, so to get around this,
   # we can use email subaddressing with this convention:
   # <email_prefix>+user<user_number>@gmail.com
   EMAIL_PREFIX=<email_prefix>

   # Mastodon client credentials
   MASTODON_CLIENT_ID=<client ID>
   MASTODON_CLIENT_SECRET=<client secret>

   # User passwords
   # Currently 5 users created, named user0001 - user0005
   USER0001_PASSWORD=<password>
   USER0002_PASSWORD=<password>
   USER0003_PASSWORD=<password>
   USER0004_PASSWORD=<password>
   USER0005_PASSWORD=<password>
   ```

3. After installation, run the `Mastodon.ipynb` notebook, selecting the `.venv` as the kernel:
4. The passwords in the `.env` file can be used to directly log in to the Mastodon server as any user at the domain [https://social-sandbox.com/](https://social-sandbox.com/). For example, for user0001, the credentials will be:

   ```txt
   E-mail address: <email_prefix>+user0001@gmail.com
   Password: <password>
   ```

### 5. Run the `Concordia+Mastodon.ipynb` example again, this time with `USE_MASTODON_SERVER = True`

With this setting turned on, when an agent invokes a Mastodon app action, that action will actually use the Mastodon API to execute on the Mastodon instance.
