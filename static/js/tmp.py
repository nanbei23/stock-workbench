with open('ai.js', 'r', encoding='utf-8', errors='surrogateescape') as f:
	c=f.read()
with open('ai.js', 'w') as f:
	f.write(c.encode('utf-8', errors='ignore').decode('utf-8'))
print('Done')
