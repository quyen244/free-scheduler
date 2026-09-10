
# flowchart
```mermaid
flowchart TD 
    A([Start]) --> B[Enter Password]
    B --> C{Password correct ?}
    C --> |Yes| D[HomePage]
    C --> |No| E[Show Error]
```

```mermaid 
flowchart TD 
    A{Logged in ?} --> |Yes| B[Homepage]
    A --> |No| C[Login Page]    
```
A[Rectangle]

B(Rounded)

C{Decision}

D[(Database)]

E((Circle))

[ ]       Process
{ }       Decision
[( )]     Database
([ ])     Start / End



# Subgraph
```mermaid
flowchart LR 
    A[User]

    subgraph Frontend 
        B[React]
    end

    subgraph Backend
        C[API]
        D[Service]
        E[(Database)]
    end

    A --> B 
    B --> C
    C --> D 
    D --> E
```


# state diagram 

```mermaid
stateDiagram-v2 

[*] --> Pending 

Pending --> Processing : process 
Processing --> Failed : error
Failed --> Processing : retry

Processing --> Completed : success 
Completed --> [*]
```


# ERD 


```mermaid
erDiagram 
User ||--o{ Order : places

User {
    int id PK
    string name 
    string email 
}


Order {
    int id PK
    int user_id FK
    decimal total
}

```


