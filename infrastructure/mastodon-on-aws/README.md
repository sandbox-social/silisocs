# Mastodon on AWS

**Note:** This README and infrastructure template are adapted from [widdix/mastodon-on-aws](https://github.com/widdix/mastodon-on-aws/).

Want to host your own Mastodon instance on AWS? Here you go.

The architecture consists of the following building blocks:

* Application Load Balancer (ALB)
* ECS and Fargate
* RDS Aurora Serverless v1
* ElastiCache (Redis)
* S3
* SES
* CloudWatch
* IAM
* KMS
* Route 53
* CloudFront

![Mastodon on AWS: Architecture](architecture.png)

Check out this blog post [Mastodon on AWS: Host your own instance](https://cloudonaut.io/mastodon-on-aws/) for more details.

## Prerequisites

Ensure the following are installed and configured on your system:

- AWS account
- AWS CLI: [Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html)
- AWS Session Manager Plugin for the AWS CLI: `brew install --cask session-manager-plugin`
- Git: [Installation Guide](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
- Docker: [Installation Guide](https://docs.docker.com/get-docker/)
- Yarn: [Installation Guide](https://classic.yarnpkg.com/en/docs/install)
- Domain Name (top-level or sub-domain where you can configure an `NS` record to delegate to Route 53 nameservers)

## Setup

### Purchase a Domain Name in Route 53

1. Purchase a domain name in Route 53 and set it as an environment variable:

    ```bash
    export DOMAIN_NAME=social-sandbox.com
    ```

### Define AWS Profile

1. Set your AWS profile:

    ```bash
    export AWS_PROFILE=austinmwelch5
    ```

### Create an S3 Bucket for Deployment

1. Create a bucket for deployment:

    ```bash
    export S3_BUCKET=mastodon-deploy
    aws s3 mb s3://$S3_BUCKET --profile $AWS_PROFILE
    ```

### Clone the Repository and Navigate to the Directory

1. Clone the repository and navigate into the appropriate directory:

    ```bash
    git clone https://github.com/social-sandbox/mastodon-sim.git
    cd mastodon-sim/infrastructure/mastodon-on-aws
    ```

### Generate Required Secrets and Keys

1. Generate the necessary secrets and keys:

    ```bash
    ./generate_parameters.sh $DOMAIN_NAME
    ```

### Install Project Dependencies

1. Download and install all the project dependencies locally into the `node_modules` directory:

    ```bash
    yarn install
    ```

### Package CloudFormation Modules

1. Package CloudFormation modules and verify their upload:

    ```bash
    ./package-cfn-modules.sh -b $S3_BUCKET -p $AWS_PROFILE
    aws s3 ls s3://$S3_BUCKET/node_modules/@cfn-modules/ --profile $AWS_PROFILE
    ```

## Deployment

### Deploy the CloudFormation Template

1. Deploy the CloudFormation template:

    ```bash
    aws cloudformation deploy \
    --template-file packaged.yml \
    --stack-name mastodon-on-aws \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides file://parameters.json \
    --profile $AWS_PROFILE
    ```

### Update the Domain Name NS Records

1. Wait 1-2 minutes, then follow these steps:
    - Go to AWS Console -> Route 53 -> Hosted Zones.
    - Select the hosted zone with the description `$DOMAIN_NAME public DNS zone`.
    - Select the Type NS and note down the 4 entries under Value (e.g., `ns-574.awsdns-07.net`).

2. Update the NS records:
    - Go to AWS Console -> Route 53 -> Registered Domains.
    - Select the domain, click Actions -> Edit Name Servers, and update the entries to match the copied values.
    - Click Save changes.

### Wait for the Stack to Finish Deploying

1. This process could take up to an hour. Monitor the progress in the AWS CloudFormation console.

## Finish Setup

### Enable the Admin User / Accessing `tootctl`

1. Access the Mastodon CLI:
    - Open Elastic Container Service (ECS) via the AWS Management Console.
    - Select the ECS cluster prefixed with the name of your CloudFormation stack (e.g., `mastodon-on-aws-*`).
    - Note down the full name of the cluster (e.g., `mastodon-on-aws-Cluster-1NHBMI9NL62QP-Cluster-pkxgiUVXxLC7`).
    - Select the Tasks tab.
    - Search for a running task with a definition containing `*-WebService-*` in its name.
    - Note down the task ID (e.g., `a752b99a4cf843ce8a957c374fc98abf`).

2. Connect to the container running the Ruby on Rails (Web) application:

    ```bash
    export CLUSTER_NAME=mastodon-on-aws-Cluster-15O0W1QN1M8TM-Cluster-b4n5zPXzNtjY
    export TASK_ID=4a349ef9932b4fab9811c7ef89bcb5d0
    aws ecs execute-command --cluster $CLUSTER_NAME --container app --command /bin/bash --interactive --task $TASK_ID --profile $AWS_PROFILE
    ```

3. After the session is established, you are ready to use the [tootctl](https://docs.joinmastodon.org/admin/tootctl/).

### Create a User and Give Admin Privileges

While on the web server, create a new user and grant admin privileges.

```bash
export ADMIN_USERNAME=yourusername
export EMAIL_ADDRESS=youremail@gmail.com

# Create a new user and generate a password (write down the password)
RAILS_ENV=production bin/tootctl accounts create $ADMIN_USERNAME --email $EMAIL_ADDRESS --confirmed --role Owner

# Change the user to owner
RAILS_ENV=production bin/tootctl accounts modify $ADMIN_USERNAME --role Owner

# Enable/approve the user
RAILS_ENV=production bin/tootctl accounts modify $ADMIN_USERNAME --enable --confirm --approve
```

### Install Python on web server

While on the web server, install Python and Mastodon package

```bash
# Update the package list
apt-get update

# Install python3-venv if it's not already installed
apt-get install python3-venv

# Create a virtual environment in the /opt/mastodon directory
python3 -m venv /opt/mastodon/venv

# Activate the virtual environment
source /opt/mastodon/venv/bin/activate

# Install packages within the virtual environment
pip install Mastodon.py loguru tqdm

# Verify the installation (optional)
pip show Mastodon.py
```

### Test Website and Admin Login

1. Go to the domain name, e.g., `https://social-sandbox.com`.
2. Sign in with the email address used above and the generated password.
3. Click the settings button, then go to Preferences.
4. Click on Administration to open the admin dashboard.

### Register an app and create a `.env` file

On local machine, register a new app and create a `.env` file:

**Note:** This only needs to be done once per server.

```bash
cd mastodon-sim
# See create_env_file.py -h for usage
uv run python src/mastodon_sim/environments/backends/mastodon/mastodon_ops/create_env_file.py
```

This should output the following:

```log
2024-07-06T07:41:45.608299-0400 INFO Creating new Mastodon app...
2024-07-06T07:41:45.608993-0400 INFO App name: MyMastodonApp
2024-07-06T07:41:45.609070-0400 INFO API base URL: https://social-sandbox.com
2024-07-06T07:41:45.609116-0400 INFO Scopes: ['read', 'write', 'follow']
2024-07-06T07:41:45.792379-0400 INFO Created new Mastodon app
2024-07-06T07:41:45.793279-0400 INFO Created new .env file at /Users/austinwelch/Desktop/mastodon-sim/.env
2024-07-06T07:41:45.793401-0400 INFO Added API_BASE_URL=https://social-sandbox.com
2024-07-06T07:41:45.793498-0400 INFO Added EMAIL_PREFIX=youremail
2024-07-06T07:41:45.793774-0400 INFO Adding new MASTODON_CLIENT_ID
2024-07-06T07:41:45.793870-0400 INFO Adding new MASTODON_CLIENT_SECRET
2024-07-06T07:41:45.794207-0400 INFO Updated .env file at /Users/austinwelch/Desktop/mastodon-sim/.env
```

## Create users

1. Log in to the web server
2. Copy/create the `infrastructure/mastodon-on-aws/webserver_scripts/create_users.py` script on the web server.
3. Activate the Python environment

    ```bash
    source /opt/mastodon/venv/bin/activate
    ```

4. Try a dry run first

    ```bash
    python3 create_users.py testemail -n 3 --dry-run
    ```

5. Run the creation script

    ```bash
    python3 create_users.py youremail -n 5
    ```

## Costs for Running Mastodon on AWS

Estimating costs for AWS is not trivial. My estimation assumes a small Mastodon instance for 1-50 users. The architecture's monthly charges are about $65 per month. The following table lists the details (us-east-1).

| Service               | Configuration    | Monthly Costs (USD) |
| --------------------- | ---------------- | ------------------: |
| ECS + Fargate         | 3 Spot Tasks     | $12.08              |
| RDS for Postgres      | t4g.micro        | $12.10              |
| ElastiCache for Redis | t4g.micro        | $11.52              |
| ALB                   | Load Balancer Hours | $16.20          |
| S3                    | 25 GB + requests | $0.58               |
| Route 53              | Hosted Zone      | $0.50               |
| **Total**             |                  | $52.97              |

Please note that the cost estimation is not complete and costs differ per region. For example, the estimation does not include network traffic, CloudWatch, SES, and domain. [Monitor your costs](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html)!

## Update

Here is how you update your infrastructure.

1. Open CloudFormation via the AWS Management Console.
2. Select the CloudFormation stack which is named `mastodon-on-aws` in case you created the stack with our defaults.
3. Press the `Edit` button.
4. Choose the option `Replace current template` with `https://s3.eu-central-1.amazonaws.com/mastodon-on-aws-cloudformation/latest/quickstart.yml`.
5. Go through the rest of the wizard and keep the defaults.


---

## Increase API Limits

Mastodon API limits are configured in the **`rack_attack.rb`** file, located inside the **Mastodon Web Service task container**. This service runs on **AWS Fargate**, backed by **AWS Elastic Container Service (ECS)**. Although direct access to this file is not needed, it’s useful to know that it resides within the running container at:  
**`/opt/mastodon/config/initializers/rack_attack.rb`**.

The deployment uses the public Mastodon Docker image:  
**`public.ecr.aws/h6i3a8b9/mastodon:v4.2.10`**

To increase the API rate limits, follow these high-level steps:

1. Modify the `rack_attack.rb` file with the desired API limits.
2. Build a custom Docker image that incorporates the modified `rack_attack.rb` file.
3. Create a new **Elastic Container Repository (ECR)** in your AWS account and push the custom image to it.
4. Update the `mastodon.yaml` configuration to point to the custom image.

---

### Detailed Steps:

1. **Delete the Current Stack:**
   1. Log in to the AWS Console.
   2. Navigate to the **CloudFormation** service and go to **Stacks**.
   3. Select the top-level Mastodon stack (the only non-nested one) and click **Delete**.  
      *Note: This may take about 5 minutes and will partially fail, as some resources need manual cleanup.*
   4. **ALB** and **Hosted Zones** will likely fail to delete automatically.  
      - **TODO:** Provide manual cleanup instructions for these resources.
   5. Once the resources are deleted, retry deleting the entire stack, which should now succeed.
   6. Wait another 5 minutes for the deletion to complete.

2. **Clear the S3 Bucket:**
   - Go to the **S3** service in the AWS Console.
   - Find the Mastodon deployment bucket and **clear its contents** (but do not delete the bucket itself).

3. **Clean Local Files:**
   - In your terminal, change to the directory:  
     `mastodon-sim/infrastructure/mastodon-on-aws`  
   - Delete the following files and directories:
     - `parameters.json`
     - `node_modules`
     - `packaged.yml`

4. **Build and Push the Custom Docker Image:**
   - Navigate to:  
     `mastodon-sim/infrastructure/mastodon-on-aws/custom_mods`  
   - Update the `rack_attack.rb` file with your new limits.
   - Run the script:  
     ```bash
     ./build_push_update.sh
     ```
   This script performs the following tasks:
   - Builds the custom Docker image for the Mastodon Web Service.
   - Pushes the new image to your private Amazon ECR repository.
   - Updates the CloudFormation template to use the custom image.

   If successful, this will generate an updated `mastodon.yaml` file at:  
   `mastodon-sim/infrastructure/mastodon-on-aws/`.

5. **Verify the Custom Image Reference:**
   - Open the `mastodon.yaml` file and confirm that the `AppImage` keys now point to the custom ECR image.

6. **Rerun the Full Setup:**
   - Follow the setup instructions at the top of the file, starting from the **Generate Required Secrets and Keys** step.

If all steps are followed correctly, the Mastodon server will redeploy with the new API rate limits in effect!

## Activating SES

In case you haven't used SES in your AWS account before, you most likely need to request production access for SES. This is required so that your Mastodon instance is able to send emails (e.g., registration, forgot password, and many more). See [Moving out of the Amazon SES sandbox](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html) to learn more.
