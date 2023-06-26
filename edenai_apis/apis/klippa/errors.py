from edenai_apis.utils.exception import (
    ProviderErrorLists,
    ProviderInvalidInputFileError
)

# NOTE: error messages should be regex patterns
ERRORS: ProviderErrorLists = {
    ProviderInvalidInputFileError: [
        r"File is not a valid image"
     ]
}