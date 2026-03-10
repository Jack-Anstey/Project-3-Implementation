# Use the AWS Python image for Lambda deployment
FROM public.ecr.aws/lambda/python:3.13

# Set working directory for Lambda
WORKDIR ${LAMBDA_TASK_ROOT}

# Copy requirements first, then install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the remaining files
COPY ./ ./

# Add the Lambda web adaptor from the quickstart guide: https://github.com/awslabs/aws-lambda-web-adapter
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.0-rc1 /lambda-adapter /opt/extensions/lambda-adapter

# Set entrypoint
ENTRYPOINT ["python", "-m", "src"]
