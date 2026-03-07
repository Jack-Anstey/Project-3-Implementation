# Use the AWS Python image for Lambda deployment
FROM public.ecr.aws/lambda/python:3.13

# Set working directory for Lambda
WORKDIR ${LAMBDA_TASK_ROOT}

# Copy requirements first, then install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the remaining files
COPY ./ ./

# Set entrypoint
ENTRYPOINT ["python", "-m", "src"]
