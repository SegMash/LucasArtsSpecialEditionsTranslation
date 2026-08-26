import sys
# Get a string from user and reverse it.
def reverse_string():
    user_input = input("Enter a string: ")
    reversed_string = user_input[::-1]
    print("Reversed string:", reversed_string)

if __name__ == "__main__":
    # get the string from command line as argument
    if len(sys.argv) > 1:
        sys.argv[1] = sys.argv[1][::-1]
        print("Reversed string:", sys.argv[1])
    else:
        reverse_string()